from __future__ import absolute_import

import logging

from django.utils import timezone
from oscar.apps.partner import availability, strategy
from oscar.core.loading import get_model
from oscar.apps.partner import prices
from decimal import Decimal as D
from ecommerce.core.constants import SEAT_PRODUCT_CLASS_NAME
from ecommerce.core.models import User

logger = logging.getLogger(__name__)


# TODO Remove unneccessary logging
# Add GST for Indian customers -mohit741

class INRPricingPolicy(prices.Base):
    currency = 'INR'

class IncludeGST(strategy.FixedRateTax, INRPricingPolicy):
    rate = D('0.18')

class CourseSeatAvailabilityPolicyMixin(strategy.StockRequired):
    """
    Availability policy for Course seats.

    Child seats are only available if the current date is not beyond the seat's enrollment close date.
    Parent seats are never available.
    """

    @property
    def seat_class(self):
        ProductClass = get_model('catalogue', 'ProductClass')
        return ProductClass.objects.get(name=SEAT_PRODUCT_CLASS_NAME)

    def availability_policy(self, product, stockrecord):
        """ A product is unavailable for non-admin users if the current date is
        beyond the product's expiration date. Products are always available for admin users.
        """

        is_staff = getattr(self.user, 'is_staff', False)
        is_available = product.expires is None or (product.expires >= timezone.now())
        if is_staff or is_available:
            return super(CourseSeatAvailabilityPolicyMixin, self).availability_policy(product, stockrecord)

        return availability.Unavailable()


# Use the second stock record for Indian Customers -mohit741
class UseSecondStockRecord(object):
    def select_stockrecord(self, product):
        try:
            return product.stockrecords.all()[1]
        except IndexError:
            return product.stockrecords.all()[0]

# Indian strategy for Indian Market -mohit741
class IndiaStrategy(UseSecondStockRecord, CourseSeatAvailabilityPolicyMixin,
                      IncludeGST, strategy.Structured):
    pass

class DefaultStrategy(strategy.UseFirstStockRecord, CourseSeatAvailabilityPolicyMixin,
                      strategy.NoTax, strategy.Structured):
    """ Default Strategy """


# Use IndiaStrategy if country is IN else use Default with no tax -mohit741
class Selector(object):
    def strategy(self, request=None, user=None, **kwargs):  # pylint: disable=unused-argument
        if request is not None:
            try:
                if user is not None and not user.is_anonymous: # Performance improvement : Call lms api only when user.country is None, and save it. -mohit741
                    if user.country is None or user.country == '':
                        profile = request.user.account_details(request)
                        _user = User.objects.get(username=user.username)
                        _user.country = profile['country']
                        _user.save()
                        logger.info('----------------------------------------Called Save Profile--------------------------------------')
                        if profile['country'] == 'IN':
                            return IndiaStrategy()
                    else:
                        if user.country == 'IN':
                            logger.info('---------------------------------Indian strategy called-------------------------------------')
                            return IndiaStrategy()
            except Exception:
                raise
        logger.info('------------------Default strategy called---------------------------')
        return DefaultStrategy(request if hasattr(request, 'user') else None)



