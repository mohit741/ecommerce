from __future__ import unicode_literals
import logging
import requests

from django.views.generic import RedirectView, View
from django.conf import settings
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect
from django.utils import six
from django.utils.translation import ugettext_lazy as _
from django.utils.decorators import method_decorator
from django.db import transaction
from django.http import JsonResponse

from oscar.apps.payment.exceptions import UnableToTakePayment
from oscar.core.loading import get_class, get_model

# from lms.djangoapps.shoppingcart.models import Order, PaidCourseRegistration

from ecommerce.extensions.checkout.mixins import EdxOrderPlacementMixin
from ecommerce.extensions.checkout.utils import get_receipt_page_url
from ecommerce.extensions.payment import facade
from ecommerce.extensions.payment.exceptions import (
    EmptyBasketException, MissingShippingAddressException,
    MissingShippingMethodException, InvalidBasket, RazorpayError)

# Load views dynamically
PaymentDetailsView = get_class('checkout.views', 'PaymentDetailsView')
CheckoutSessionMixin = get_class('checkout.session', 'CheckoutSessionMixin')
OrderTotalCalculator = get_class('checkout.calculators', 'OrderTotalCalculator')
NoShippingRequired = get_class('shipping.methods', 'NoShippingRequired')
Country = get_model('address', 'Country')
Basket = get_model('basket', 'Basket')
Repository = get_class('shipping.repository', 'Repository')
Selector = get_class('partner.strategy', 'Selector')
Source = get_model('payment', 'Source')
SourceType = get_model('payment', 'SourceType')


Applicator = get_class('offer.applicator', 'Applicator')

logger = logging.getLogger('razorpay')


class PaymentView(EdxOrderPlacementMixin, View):
    """
    Show the razorpay payment page and record the start of a transaction.
    """

    template_name = 'payment/razorpay.html'
    @property
    def payment_processor(self):
        return RazorPay(self.request.site)

    # Disable atomicity for the view. Otherwise, we'd be unable to commit to the database
    # until the request had concluded; Django will refuse to commit when an atomic() block
    # is active, since that would break atomicity. Without an order present in the database
    # at the time fulfillment is attempted, asynchronous order fulfillment tasks will fail.
    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, request, *args, **kwargs):
        return super(PaymentView, self).dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        try:
            basket = self.build_submission()['basket']
            if basket.is_empty:
                raise EmptyBasketException()
        except InvalidBasket as e:
            messages.warning(self.request, six.text_type(e))
            return reverse('basket:summary')
        except EmptyBasketException:
            messages.error(self.request, _("Your basket is empty"))
            return reverse('basket:summary')
        except MissingShippingAddressException:
            messages.error(
                self.request, _("A shipping address must be specified"))
            return reverse('checkout:shipping-address')
        except MissingShippingMethodException:
            messages.error(
                self.request, _("A shipping method must be specified"))
            return reverse('checkout:shipping-method')
        else:
            # Freeze the basket so it can't be edited while the customer is
            # making the payment
            basket.freeze()

            logger.info("Starting payment for basket #%s", basket.id)
            context = self._start_razorpay_txn(basket)
            return JsonResponse(context, status=201)
            # return render(request, self.template_name, context)

    def _start_razorpay_txn(self, basket, **kwargs):
        """
        Record the start of a transaction.
        """
        if basket.is_empty:
            raise EmptyBasketException()
        logger.info("SKU : %s", basket.lines.first().product.stockrecords.first().partner_sku)
        shipping_method = NoShippingRequired()
        shipping_charge = shipping_method.calculate(basket)
        order_total = OrderTotalCalculator().calculate(basket, shipping_charge)
        user = self.request.user
        amount = int(order_total.incl_tax * 100)
        currency = getattr(settings, 'RAZORPAY_CURRENCY', 'INR')
        if self.request.user.is_authenticated():
            email = self.request.user.email
        else:
            email = self.build_submission()['order_kwargs']['guest_email']
            user = None
        order_id = facade.create_razorpay_order(amount, currency)
        txn = facade.start_razorpay_txn(basket, order_total.incl_tax, user, email, order_id)
        sku = 'many'
        if basket.num_lines==1:
            sku = basket.lines.first().product.stockrecords.first().partner_sku
        context = {
            # "basket": basket,
            "user": user.username,
            "sku": sku,
            "amount": amount,  # amount in paisa as int
            "rz_key": settings.RAZORPAY_API_KEY,
            "order_id": order_id,
            "email": email,
            "txn_id": txn.txnid,
            "name": getattr(settings, "RAZORPAY_VENDOR_NAME", "My Store"),
            "description": getattr(
                settings, "RAZORPAY_DESCRIPTION", "Amazing Product"
            ),
            "theme_color": getattr(
                settings, "RAZORPAY_THEME_COLOR", "#F37254"
            ),
            "logo_url": getattr(
                settings, "RAZORPAY_VENDOR_LOGO",
                "https://via.placeholder.com/150x150"),
        }
        return context


class CancelResponseView(RedirectView):
    permanent = False

    def get(self, request, *args, **kwargs):
        basket = get_object_or_404(Basket, id=kwargs['basket_id'],
                                   status=Basket.FROZEN)
        basket.thaw()
        logger.info("Payment cancelled - basket #%s thawed", basket.id)
        return super(CancelResponseView, self).get(request, *args, **kwargs)

    def get_redirect_url(self, **kwargs):
        messages.error(self.request, _("Razorpay transaction cancelled"))
        return reverse('basket:summary')


class SuccessResponseView(PaymentDetailsView, EdxOrderPlacementMixin):
    preview = True

    @property
    def pre_conditions(self):
        return []

    def get(self, request, *args, **kwargs):
        """
        Fetch details about the successful transaction from Razorpay and place
        an order.
        """
        logger.info("Success response in Razorpay")
        try:
            self.rz_id = request.GET['rz_id']
            self.txn_id = request.GET['txn_id']
        except KeyError:
            # Manipulation - redirect to basket page with warning message
            logger.warning("Missing GET params on success response page")
            messages.error(
                self.request,
                _("Unable to determine Razorpay transaction details"))
            return HttpResponseRedirect(reverse('basket:summary'))

        try:
            self.txn = facade.update_transaction_details(
                self.rz_id, self.txn_id
            )
        except RazorpayError:
            logger.warning("Error in Razorpay update")
            messages.error(
                self.request,
                _("A problem occurred communicating with Razorpay - "
                  "please try again later"))
            return HttpResponseRedirect(reverse('basket:summary'))

        # Reload frozen basket which is specified in the URL
        kwargs['basket'] = self.load_frozen_basket(kwargs['basket_id'])
        if not kwargs['basket']:
            logger.warning(
                "Unable to load frozen basket with ID %s", kwargs['basket_id'])
            messages.error(
                self.request,
                _("No basket was found that corresponds to your "
                  "Razorpay transaction"))
            return HttpResponseRedirect(reverse('basket:summary'))

        logger.info(
            "Basket #%s - showing preview payment id %s",
            kwargs['basket'].id, self.rz_id)

        basket = kwargs['basket']
        '''submission = self.build_submission(basket=basket)
        return self.submit(**submission)'''
        receipt_url = get_receipt_page_url(
            order_number=basket.order_number,
            site_configuration=basket.site.siteconfiguration,
            disable_back_button=True,
        )
        try:
            order = self.create_order(request, basket)
        except Exception:  # pylint: disable=broad-except
            # any errors here will be logged in the create_order method. If we wanted any
            # Paypal specific logging for this error, we would do that here.
            return redirect(receipt_url)

        try:
            self.handle_post_order(order)
        except Exception:  # pylint: disable=broad-except
            self.log_order_placement_exception(basket.order_number, basket.id)

        return redirect(receipt_url)

    def load_frozen_basket(self, basket_id):
        # Lookup the frozen basket that this txn corresponds to
        try:
            basket = Basket.objects.get(id=basket_id, status=Basket.FROZEN)
        except Basket.DoesNotExist:
            return None
        # Assign strategy to basket instance
        if Selector:
            basket.strategy = Selector().strategy(self.request)
        # Re-apply any offers
        Applicator().apply(request=self.request, basket=basket)
        return basket

    def build_submission(self, **kwargs):
        submission = super(
            SuccessResponseView, self).build_submission(**kwargs)
        # Pass the user email so it can be stored with the order
        submission['order_kwargs']['guest_email'] = self.txn.email
        # Pass PP params
        submission['payment_kwargs']['rz_id'] = self.rz_id
        submission['payment_kwargs']['txn'] = self.txn
        return submission

    def handle_payment(self, order_number, total, **kwargs):
        """
        Capture the money from the initial transaction.
        """
        try:
            confirm_txn = facade.capture_transaction(kwargs["rz_id"])
        except RazorpayError:
            raise UnableToTakePayment()
        if not confirm_txn.is_successful:
            raise UnableToTakePayment()

        # Record payment source and event
        source_type, is_created = SourceType.objects.get_or_create(
            name='Razorpay')
        source = Source(source_type=source_type,
                        currency=confirm_txn.currency,
                        amount_allocated=confirm_txn.amount,
                        amount_debited=confirm_txn.amount)
        self.add_payment_source(source)
        self.add_payment_event('Settled', confirm_txn.amount,
                               reference=confirm_txn.rz_id)
