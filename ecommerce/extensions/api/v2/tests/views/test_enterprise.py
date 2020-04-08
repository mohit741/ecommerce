# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import datetime
import json
from collections import Counter
from uuid import uuid4

import bleach
import ddt
import httpretty
import mock
import rules
import six  # pylint: disable=ungrouped-imports
from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.timezone import now
from oscar.core.loading import get_model
from oscar.test import factories
from rest_framework import status
from six.moves import range
from slumber.exceptions import SlumberHttpBaseException

from ecommerce.core.constants import (
    ALL_ACCESS_CONTEXT,
    ENTERPRISE_COUPON_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE
)
from ecommerce.core.models import EcommerceFeatureRole, EcommerceFeatureRoleAssignment
from ecommerce.core.url_utils import get_ecommerce_url
from ecommerce.coupons.tests.mixins import CouponMixin, DiscoveryMockMixin
from ecommerce.coupons.utils import is_coupon_available
from ecommerce.courses.tests.factories import CourseFactory
from ecommerce.enterprise.benefits import BENEFIT_MAP as ENTERPRISE_BENEFIT_MAP
from ecommerce.enterprise.conditions import AssignableEnterpriseCustomerCondition
from ecommerce.enterprise.rules import request_user_has_explicit_access, request_user_has_implicit_access
from ecommerce.enterprise.tests.mixins import EnterpriseServiceMockMixin
from ecommerce.extensions.catalogue.tests.mixins import DiscoveryTestMixin
from ecommerce.extensions.offer.constants import (
    OFFER_ASSIGNMENT_EMAIL_BOUNCED,
    OFFER_ASSIGNMENT_REVOKED,
    VOUCHER_NOT_ASSIGNED,
    VOUCHER_NOT_REDEEMED,
    VOUCHER_PARTIAL_REDEEMED,
    VOUCHER_REDEEMED
)
from ecommerce.extensions.payment.models import EnterpriseContractMetadata
from ecommerce.invoice.models import Invoice
from ecommerce.programs.custom import class_path
from ecommerce.tests.mixins import JwtMixin, ThrottlingMixin
from ecommerce.tests.testcases import TestCase

Basket = get_model('basket', 'Basket')
Benefit = get_model('offer', 'Benefit')
OfferAssignment = get_model('offer', 'OfferAssignment')
OfferAssignmentEmailTemplates = get_model('offer', 'OfferAssignmentEmailTemplates')
Product = get_model('catalogue', 'Product')
Voucher = get_model('voucher', 'Voucher')
VoucherApplication = get_model('voucher', 'VoucherApplication')

ENTERPRISE_COUPONS_LINK = reverse('api:v2:enterprise-coupons-list')
OFFER_ASSIGNMENT_SUMMARY_LINK = reverse('api:v2:enterprise-offer-assignment-summary-list')
TEMPLATE_GREETING = 'hello there '
TEMPLATE_CLOSING = ' kind regards'


class TestEnterpriseCustomerView(EnterpriseServiceMockMixin, TestCase):

    dummy_enterprise_customer_data = [
        {
            'name': 'Starfleet Academy',
            'uuid': '5113b17bf79f4b5081cf3be0009bc96f',
        },
        {
            'name': 'Millennium Falcon',
            'uuid': 'd1fb990fa2784a52a44cca1118ed3993',
        }
    ]

    @mock.patch('ecommerce.enterprise.utils.EdxRestApiClient')
    @httpretty.activate
    def test_get_customers(self, mock_client):
        self.mock_access_token_response()
        instance = mock_client.return_value
        setattr(
            instance,
            'enterprise-customer',
            mock.MagicMock(
                basic_list=mock.MagicMock(
                    get=mock.MagicMock(
                        return_value=self.dummy_enterprise_customer_data
                    )
                )
            ),
        )
        url = reverse('api:v2:enterprise:enterprise_customers')
        result = self.client.get(url)
        self.assertEqual(result.status_code, status.HTTP_401_UNAUTHORIZED)

        user = self.create_user(is_staff=True)

        self.client.login(username=user.username, password=self.password)

        result = self.client.get(url)
        self.assertEqual(result.status_code, status.HTTP_200_OK)
        self.assertJSONEqual(
            result.content.decode('utf-8'),
            self.dummy_enterprise_customer_data
        )


class TestEnterpriseCustomerCatalogsViewSet(EnterpriseServiceMockMixin, TestCase):

    def setUp(self):
        super(TestEnterpriseCustomerCatalogsViewSet, self).setUp()
        user = self.create_user(is_staff=True)
        self.client.login(username=user.username, password=self.password)

        self.test_server_url = 'http://testserver.fake/'
        self.enterprise = '6ae013d4-c5c4-474d-8da9-0e559b2448e2'
        self.dummy_enterprise_customer_catalogs_data = {
            'count': 2,
            'num_pages': 1,
            'current_page': 1,
            'start': 0,
            'next': '{}?enterprise_customer={}&page=3'.format(self.ENTERPRISE_CATALOG_URL, self.enterprise),
            'previous': '{}?enterprise_customer={}&page=1'.format(self.ENTERPRISE_CATALOG_URL, self.enterprise),
            'results': [
                {
                    'enterprise_customer': self.enterprise,
                    'uuid': '869d26dd-2c44-487b-9b6a-24eee973f9a4',
                    'title': 'batman_catalog'
                },
                {
                    'enterprise_customer': self.enterprise,
                    'uuid': '1a61de70-f8e8-4e8c-a76e-01783a930ae6',
                    'title': 'new catalog'
                }
            ]
        }

        self.enterprise_catalog = '869d26dd-2c44-487b-9b6a-24eee973f9a4'
        self.dummy_enterprise_customer_catalog_data = {
            "count": 1,
            "enterprise_customer": "6ae013d4-c5c4-474d-8da9-0e559b2448e2",
            "uuid": "869d26dd-2c44-487b-9b6a-24eee973f9a4",
            "title": "batman_catalog",
            "results": [
                {
                    "organizations": [
                        "edX: "
                    ],
                    "card_image_url": None,
                    "uuid": "c068b161-ffa3-4df7-90f7-5ee6b3fa3356",
                    "title": "edX Demonstration Course",
                    "languages": None,
                    "subjects": [],
                    "content_type": "course",
                    "aggregation_key": "course:edX+DemoX",
                    "key": "edX+DemoX",
                    "short_description": None,
                    "enrollment_url": "http://lms/enterprise/6ae013d4/course/edX+DemoX/enroll/?catalog=869d26dd",
                    "full_description": None,
                    "course_runs": [
                        {
                            "enrollment_mode": "verified",
                            "end": None,
                            "go_live_date": None,
                            "enrollment_start": "2017-02-11T00:00:00Z",
                            "start": "2018-02-05T05:00:00Z",
                            "pacing_type": "instructor_paced",
                            "key": "course-v1:edX+DemoX+Demo_Course",
                            "enrollment_end": "2019-02-11T00:00:00Z",
                            "availability": "Current"
                        }
                    ]
                }
            ],
            "next": '{}{}?page=3'.format(self.ENTERPRISE_CATALOG_URL, self.enterprise_catalog),
            "previous": '{}{}?page=1'.format(self.ENTERPRISE_CATALOG_URL, self.enterprise_catalog)
        }

    @mock.patch('ecommerce.enterprise.utils.EdxRestApiClient')
    @httpretty.activate
    def test_get_customer_catalogs(self, mock_client):
        """
        Tests that `EnterpriseCustomerCatalogsViewSet`get endpoint works as expected
        """
        self.mock_access_token_response()

        instance = mock_client.return_value
        setattr(
            instance,
            'enterprise_catalogs',
            mock.MagicMock(
                get=mock.MagicMock(
                    return_value=self.dummy_enterprise_customer_catalogs_data
                )
            ),
        )

        url = reverse('api:v2:enterprise:enterprise_customer_catalogs')
        result = self.client.get(url)

        self.assertEqual(result.status_code, status.HTTP_200_OK)

        updated_response = dict(
            self.dummy_enterprise_customer_catalogs_data,
            next='{}api/v2/enterprise/customer_catalogs?enterprise_customer={}&page=3'.format(
                self.test_server_url,
                self.enterprise
            ),
            previous="{}api/v2/enterprise/customer_catalogs?enterprise_customer={}&page=1".format(
                self.test_server_url,
                self.enterprise
            ),
        )

        self.assertJSONEqual(result.content.decode('utf-8'), updated_response)

    @httpretty.activate
    def test_retrieve_customer_catalog(self):
        """
        Tests that `EnterpriseCustomerCatalogsViewSet` retrieve endpoint works as expected
        """
        self.mock_access_token_response()
        self.mock_enterprise_catalog_api_get(self.enterprise_catalog, self.dummy_enterprise_customer_catalog_data)

        url = reverse(
            'api:v2:enterprise:enterprise_customer_catalog_details',
            kwargs={'enterprise_catalog_uuid': self.enterprise_catalog}
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response_with_updated_urls = dict(
            self.dummy_enterprise_customer_catalog_data,
            next='{}api/v2/enterprise/customer_catalogs/{}?page=3'.format(
                self.test_server_url,
                self.enterprise_catalog
            ),
            previous="{}api/v2/enterprise/customer_catalogs/{}?page=1".format(
                self.test_server_url,
                self.enterprise_catalog
            ),
        )
        self.assertJSONEqual(response.content.decode('utf-8'), response_with_updated_urls)

    def test_retrieve_customer_catalog_with_exception(self):
        """
        Tests that `EnterpriseCustomerCatalogsViewSet` retrieve endpoint works as expected on exception
        """
        url = reverse(
            'api:v2:enterprise:enterprise_customer_catalog_details',
            kwargs={'enterprise_catalog_uuid': self.enterprise_catalog}
        )

        mock_path = 'ecommerce.extensions.api.v2.views.enterprise.get_enterprise_catalog'
        with mock.patch(mock_path) as mock_get_enterprise_catalog:
            mock_get_enterprise_catalog.side_effect = SlumberHttpBaseException('Insecure connection')
            with mock.patch('ecommerce.extensions.api.v2.views.enterprise.logger') as mock_logger:
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
                self.assertJSONEqual(
                    response.content.decode('utf-8'),
                    {'error': 'Unable to retrieve enterprise catalog. Exception: Insecure connection'}
                )
                self.assertTrue(mock_logger.exception.called)


@ddt.ddt
class EnterpriseCouponViewSetRbacTests(
        CouponMixin,
        DiscoveryTestMixin,
        DiscoveryMockMixin,
        JwtMixin,
        ThrottlingMixin,
        TestCase):
    """
    Test the enterprise coupon order functionality with role based access control.
    """
    def setUp(self):
        super(EnterpriseCouponViewSetRbacTests, self).setUp()
        self.user = self.create_user(is_staff=True)
        self.client.login(username=self.user.username, password=self.password)

        self.data = {
            'benefit_type': Benefit.PERCENTAGE,
            'benefit_value': 100,
            'category': {'name': self.category.name},
            'code': '',
            'end_datetime': str(now() + datetime.timedelta(days=10)),
            'price': 100,
            'quantity': 2,
            'start_datetime': str(now() - datetime.timedelta(days=10)),
            'title': 'Tešt Enterprise čoupon',
            'voucher_type': Voucher.SINGLE_USE,
            'enterprise_customer': {'name': 'test enterprise', 'id': six.text_type(uuid4())},
            'enterprise_customer_catalog': six.text_type(uuid4()),
            'notify_email': 'batman@gotham.comics',
            'contract_discount_type': EnterpriseContractMetadata.PERCENTAGE,
            'contract_discount_value': '12.35',
        }

        self.course = CourseFactory(id='course-v1:test-org+course+run', partner=self.partner)
        self.verified_seat = self.course.create_or_update_seat('verified', False, 100)
        self.enterprise_slug = 'batman'
        self.role = EcommerceFeatureRole.objects.get(name=ENTERPRISE_COUPON_ADMIN_ROLE)
        EcommerceFeatureRoleAssignment.objects.get_or_create(
            role=self.role,
            user=self.user,
            enterprise_id=self.data['enterprise_customer']['id']
        )
        self.set_jwt_cookie(
            system_wide_role=SYSTEM_ENTERPRISE_ADMIN_ROLE, context=self.data['enterprise_customer']['id']
        )
        patcher = mock.patch('ecommerce.extensions.api.v2.utils.send_mail')
        self.send_mail_patcher = patcher.start()
        self.addCleanup(patcher.stop)

    def get_coupon_voucher(self, coupon):
        """
        Helper method to get coupon voucher.
        """
        return coupon.attr.coupon_vouchers.vouchers.first()

    def get_coupon_data(self, coupon_title):
        """
        Helper method to return coupon data by coupon title.
        """
        coupon = Product.objects.get(title=coupon_title)
        return {
            'end_date': self.get_coupon_voucher_end_date(coupon),
            'errors': [],
            'id': coupon.id,
            'max_uses': 2,
            'num_codes': 2,
            'num_unassigned': 2,
            'num_uses': 0,
            'start_date': self.get_coupon_voucher_start_date(coupon),
            'title': coupon.title,
            'usage_limitation': 'Single use',
            'available': is_coupon_available(coupon),
        }

    def get_coupon_voucher_start_date(self, coupon):
        """
        Helper method to return coupon voucher start date.
        """
        return self.get_coupon_voucher(coupon).start_datetime.isoformat().replace('+00:00', 'Z')

    def get_coupon_voucher_end_date(self, coupon):
        """
        Helper method to return coupon voucher end date.
        """
        return self.get_coupon_voucher(coupon).end_datetime.isoformat().replace('+00:00', 'Z')

    def get_response(self, method, path, data=None):
        """
        Helper method for sending requests and returning the response.
        """
        enterprise_id = ''
        enterprise_name = 'ToyX'
        if data and isinstance(data, dict) and data.get('enterprise_customer'):
            enterprise_id = data['enterprise_customer']['id']
            enterprise_name = data['enterprise_customer']['name']

        with mock.patch('ecommerce.extensions.voucher.utils.get_enterprise_customer') as patch1:
            with mock.patch('ecommerce.extensions.api.v2.utils.get_enterprise_customer') as patch2:
                patch1.return_value = patch2.return_value = {
                    'name': enterprise_name,
                    'enterprise_customer_uuid': enterprise_id,
                    'slug': self.enterprise_slug,
                }
                if method == 'GET':
                    return self.client.get(path, data=data)
                if method == 'POST':
                    return self.client.post(path, json.dumps(data), 'application/json')
                if method == 'PUT':
                    return self.client.put(path, json.dumps(data), 'application/json')
        return None

    def get_response_json(self, method, path, data=None):
        """
        Helper method for sending requests and returning JSON response content.
        """
        response = self.get_response(method, path, data)
        if response:
            return json.loads(response.content.decode('utf-8'))
        return None

    def assert_new_codes_email(self):
        """
        Verify that new codes email is sent as expected
        """
        self.send_mail_patcher.assert_called_with(
            subject=settings.NEW_CODES_EMAIL_CONFIG['email_subject'],
            message=settings.NEW_CODES_EMAIL_CONFIG['email_body'].format(enterprise_slug=self.enterprise_slug),
            from_email=settings.NEW_CODES_EMAIL_CONFIG['from_email'],
            recipient_list=[self.data['notify_email']],
            fail_silently=False
        )

    def test_new_codes_email_for_enterprise_coupon(self):
        """"
        Test that new codes emails is sent with correct data upon enterprise coupon creation.
        """
        self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.assert_new_codes_email()

    def test_list_enterprise_coupons(self):
        self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.create_coupon()
        self.assertEqual(Product.objects.filter(product_class__name='Coupon').count(), 2)

        response = self.client.get(ENTERPRISE_COUPONS_LINK)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        coupon_data = json.loads(response.content.decode('utf-8'))['results']
        self.assertEqual(len(coupon_data), 1)
        self.assertEqual(coupon_data[0]['title'], self.data['title'])
        self.assertEqual(coupon_data[0]['client'], self.data['enterprise_customer']['name'])
        self.assertEqual(coupon_data[0]['enterprise_customer'], self.data['enterprise_customer']['id'])
        self.assertEqual(coupon_data[0]['enterprise_customer_catalog'], self.data['enterprise_customer_catalog'])
        self.assertEqual(coupon_data[0]['code_status'], 'ACTIVE')

    def test_create_ent_offers(self):
        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        coupon = Product.objects.get(title=self.data['title'])
        enterprise_customer_id = self.data['enterprise_customer']['id']
        enterprise_name = self.data['enterprise_customer']['name']
        enterprise_catalog_id = self.data['enterprise_customer_catalog']
        self.assertEqual(coupon.attr.enterprise_customer_uuid, enterprise_customer_id)
        vouchers = coupon.attr.coupon_vouchers.vouchers.all()
        for voucher in vouchers:
            all_offers = voucher.offers.all()
            self.assertEqual(len(all_offers), 1)
            offer = all_offers[0]
            self.assertEqual(str(offer.condition.enterprise_customer_uuid), enterprise_customer_id)
            self.assertEqual(str(offer.condition.enterprise_customer_catalog_uuid), enterprise_catalog_id)
            self.assertEqual(offer.condition.proxy_class, class_path(AssignableEnterpriseCustomerCondition))
            self.assertIsNone(offer.condition.range)
            self.assertEqual(offer.benefit.proxy_class, class_path(ENTERPRISE_BENEFIT_MAP[self.data['benefit_type']]))
            self.assertEqual(offer.benefit.value, self.data['benefit_value'])
            self.assertIsNone(offer.benefit.range)

        # Check that the enterprise name took precedence as the client name
        basket = Basket.objects.filter(lines__product_id=coupon.id).first()
        invoice = Invoice.objects.get(order__basket=basket)
        self.assertEqual(invoice.business_client.name, enterprise_name)

    def test_update_ent_offers(self):
        self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        coupon = Product.objects.get(title=self.data['title'])

        self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'title': 'Updated Enterprise Coupon',
            }
        )
        updated_coupon = Product.objects.get(title='Updated Enterprise Coupon')
        self.assertEqual(coupon.id, updated_coupon.id)

    def test_update_non_ent_coupon(self):
        coupon = self.create_coupon()
        response = self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'title': 'Updated Enterprise Coupon',
            }
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_max_uses_single_use(self):
        self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        coupon = Product.objects.get(title=self.data['title'])
        response = self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'max_uses': 5,
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_max_uses_invalid_value(self):
        self.data.update({
            'voucher_type': Voucher.MULTI_USE,
            'max_uses': 5,
        })
        self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        coupon = Product.objects.get(title=self.data['title'])
        response = self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'max_uses': -5,
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def assert_coupon_codes_response(self, response, coupon_id, max_uses, results_count, pagination=None, is_csv=False):
        """
        Verify response received from `/api/v2/enterprise/coupons/{coupon_id}/codes/` endpoint
        """
        coupon = Product.objects.get(id=coupon_id)
        all_coupon_codes = coupon.attr.coupon_vouchers.vouchers.values_list('code', flat=True)
        if is_csv:
            total_result_count = len(response)
            all_received_codes = [result.split(',')[1] for result in response if result]
            all_received_code_max_uses = [int(result.split(',')[3]) for result in response if result]
        else:
            total_result_count = len(response['results'])
            all_received_codes = [result['code'] for result in response['results']]
            all_received_code_max_uses = [result['redemptions']['total'] for result in response['results']]

        # `max_uses` should be same for all codes
        max_uses = max_uses or 1
        self.assertEqual(set(all_received_code_max_uses), set([max_uses]))

        # total count of results returned is correct
        self.assertEqual(total_result_count, results_count)

        # all received codes must be equals to coupon codes
        self.assertTrue(set(all_received_codes).issubset(all_coupon_codes))

        if pagination:
            self.assertEqual(response['count'], pagination['count'])
            self.assertEqual(response['current_page'], pagination['current_page'])
            self.assertEqual(response['num_pages'], pagination['num_pages'])
            self.assertEqual(response['next'], pagination['next'])
            self.assertEqual(response['previous'], pagination['previous'])

    def assign_coupon_codes(self, coupon_id, vouchers, code_assignments=None):
        """
        Assigns codes.
        """
        for i, voucher in enumerate(vouchers):
            if code_assignments[i] == 0:
                continue

            # For multi-use-per-customer case, email list should be same.
            if voucher.usage == Voucher.MULTI_USE_PER_CUSTOMER:
                emails = ['user@example.com']
            else:
                emails = [
                    'user{email_index}@example.com'.format(email_index=email_index)
                    for email_index in range(code_assignments[i])
                ]

            self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'emails': emails,
                    'codes': [voucher.code],
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING
                }
            )

    def use_voucher(self, voucher, user):
        """
        Mark voucher as used by provided user
        """
        order = factories.OrderFactory()
        order_line = factories.OrderLineFactory(product=self.verified_seat, partner_sku='test_sku')
        order.lines.add(order_line)
        voucher.record_usage(order, user)
        voucher.offers.first().record_usage(discount={'freq': 1, 'discount': 1})

    def create_coupon_with_applications(self, coupon_data, voucher_type, quantity, max_uses):
        """
        Create coupon and voucher applications(redemeptions).
        """
        # create coupon
        coupon_post_data = dict(coupon_data, voucher_type=voucher_type, quantity=quantity, max_uses=max_uses)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()

        # create some voucher applications
        coupon_id = coupon['coupon_id']
        coupon = Product.objects.get(id=coupon_id)
        for voucher in coupon.attr.coupon_vouchers.vouchers.all():
            for _ in range(max_uses or 1):
                self.use_voucher(voucher, self.create_user())

        return coupon_id

    def assert_code_detail_response(self, response, expected, codes):
        self.assertEqual(len(response), len(expected))
        expected_response = []
        for result in expected:
            expected_result = result
            expected_result['code'] = codes[result['code']]
            expected_result['redeem_url'] = '{url}?code={code}'.format(
                url=get_ecommerce_url('/coupons/offer/'),
                code=expected_result['code']
            )
            expected_response.append(expected_result)

        response = sorted(response, key=lambda k: (k['code'], k['assigned_to']))
        expected_response = sorted(expected_response, key=lambda k: (k['code'], k['assigned_to']))
        self.assertEqual(response, expected_response)

    def assign_user_to_code(self, coupon_id, emails, codes):
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay'):
            self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': emails,
                    'codes': codes
                }
            )

    @ddt.data(
        {
            'voucher_type': Voucher.SINGLE_USE,
            'quantity': 3,
            'max_uses': None,
            'code_assignments': {'user1@example.com': 1},
            'code_redemptions': {'user2@example.com': {'code': 2, 'num': 1}},
            'expected_responses': {
                VOUCHER_NOT_ASSIGNED: [
                    {'code': 0, 'assigned_to': '', 'redemptions': {'used': 0, 'total': 1, 'num_assignments': 0}}
                ],
                VOUCHER_NOT_REDEEMED: [
                    {'code': 1, 'assigned_to': 'user1@example.com', 'redemptions': {'used': 0, 'total': 1}}
                ],
                VOUCHER_PARTIAL_REDEEMED: [],
                VOUCHER_REDEEMED: [
                    {'code': 2, 'assigned_to': 'user2@example.com', 'redemptions': {'used': 1, 'total': 1}}
                ]
            }
        },
        {
            'voucher_type': Voucher.MULTI_USE_PER_CUSTOMER,
            'quantity': 4,
            'max_uses': 2,
            'code_assignments': {'user1@example.com': 1, 'user2@example.com': 2},
            'code_redemptions': {
                'user2@example.com': {'code': 2, 'num': 1},
                'user3@example.com': {'code': 3, 'num': 2}
            },
            'expected_responses': {
                VOUCHER_NOT_ASSIGNED: [
                    {'code': 0, 'assigned_to': '', 'redemptions': {'used': 0, 'total': 2, 'num_assignments': 0}}
                ],
                VOUCHER_NOT_REDEEMED: [
                    {'code': 1, 'assigned_to': 'user1@example.com', 'redemptions': {'used': 0, 'total': 2}}
                ],
                VOUCHER_PARTIAL_REDEEMED: [
                    {'code': 2, 'assigned_to': 'user2@example.com', 'redemptions': {'used': 1, 'total': 2}}
                ],
                VOUCHER_REDEEMED: [
                    {'code': 3, 'assigned_to': 'user3@example.com', 'redemptions': {'used': 2, 'total': 2}}
                ]
            }
        },
        {
            'voucher_type': Voucher.MULTI_USE,
            'quantity': 3,
            'max_uses': 4,
            'code_assignments': {'user1@example.com': 1, 'user2@example.com': 1},
            'code_redemptions': {
                'user2@example.com': {'code': 1, 'num': 1},
                'user3@example.com': {'code': 2, 'num': 2},
                'user4@example.com': {'code': 2, 'num': 2},
            },
            'expected_responses': {
                VOUCHER_NOT_ASSIGNED: [
                    {'code': 0, 'assigned_to': '', 'redemptions': {'used': 0, 'total': 4, 'num_assignments': 0}},
                    {'code': 1, 'assigned_to': '', 'redemptions': {'used': 1, 'total': 4, 'num_assignments': 2}}
                ],
                VOUCHER_NOT_REDEEMED: [
                    {'code': 1, 'assigned_to': 'user1@example.com', 'redemptions': {'used': 0, 'total': 1}}
                ],
                VOUCHER_PARTIAL_REDEEMED: [
                    {'code': 1, 'assigned_to': 'user2@example.com', 'redemptions': {'used': 1, 'total': 2}}
                ],
                VOUCHER_REDEEMED: [
                    {'code': 2, 'assigned_to': 'user3@example.com', 'redemptions': {'used': 2, 'total': 2}},
                    {'code': 2, 'assigned_to': 'user4@example.com', 'redemptions': {'used': 2, 'total': 2}}
                ]
            }
        },
        {
            'voucher_type': Voucher.ONCE_PER_CUSTOMER,
            'quantity': 2,
            'max_uses': 3,
            'code_assignments': {'user1@example.com': 1},
            'code_redemptions': {'user2@example.com': {'code': 1, 'num': 1}},
            'expected_responses': {
                VOUCHER_NOT_ASSIGNED: [
                    {'code': 0, 'assigned_to': '', 'redemptions': {'used': 0, 'total': 3, 'num_assignments': 0}},
                    {'code': 1, 'assigned_to': '', 'redemptions': {'used': 1, 'total': 3, 'num_assignments': 1}}
                ],
                VOUCHER_NOT_REDEEMED: [
                    {'code': 1, 'assigned_to': 'user1@example.com', 'redemptions': {'used': 0, 'total': 1}}
                ],
                VOUCHER_PARTIAL_REDEEMED: [],
                VOUCHER_REDEEMED: [
                    {'code': 1, 'assigned_to': 'user2@example.com', 'redemptions': {'used': 1, 'total': 1}}
                ]
            }
        },
    )
    @ddt.unpack
    def test_coupon_codes_detail(
            self,
            voucher_type,
            quantity,
            max_uses,
            code_assignments,
            code_redemptions,
            expected_responses):
        """
        Tests the expected response for the code details endpoint based on various types and usage states for a coupon.
        :param voucher_type: Usage type for vouchers to be created.
        :param quantity: Number of vouchers to create.
        :param max_uses: Number of usages per voucher.
        :param code_assignments: Mapping of user emails to be assigned to a particular code, indicated by its index.
        :param code_redemptions: Mapping of user emails to a code index and the # of redemptions to make for that user.
        :param expected_responses: Mapping of code filter to the expected response for that filter.
        :return:
        """
        coupon_post_data = dict(self.data, voucher_type=voucher_type, quantity=quantity, max_uses=max_uses)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        vouchers = Product.objects.get(id=coupon_id).attr.coupon_vouchers.vouchers.all()
        codes = [voucher.code for voucher in vouchers]

        for email, code_index in six.iteritems(code_assignments):
            self.assign_user_to_code(coupon_id, [email], [codes[code_index]])

        for email, data in six.iteritems(code_redemptions):
            redeeming_user = self.create_user(email=email)
            for _ in range(0, data['num']):
                self.use_voucher(Voucher.objects.get(code=codes[data['code']]), redeeming_user)

        for code_filter, expected_response in six.iteritems(expected_responses):
            response = self.get_response(
                'GET',
                '/api/v2/enterprise/coupons/{}/codes/?code_filter={}'.format(coupon_id, code_filter)
            ).json()
            self.assert_code_detail_response(response['results'], expected_response, codes)

    def test_coupon_codes_detail_with_invalid_coupon_id(self):
        """
        Verify that `/api/v2/enterprise/coupons/{coupon_id}/codes/` endpoint returns 400 on invalid coupon id
        """
        response = self.get_response('GET', '/api/v2/enterprise/coupons/1212121212/codes/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_coupon_codes_detail_with_invalid_code_filter(self):
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=1, max_uses=None)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']

        response = self.get_response(
            'GET',
            '/api/v2/enterprise/coupons/{}/codes/?code_filter={}'.format(coupon_id, 'invalid-filter')
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = response.json()
        assert response == ['Invalid code_filter specified: invalid-filter']

    def test_coupon_codes_detail_with_no_code_filter(self):
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=1, max_uses=None)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']

        response = self.get_response(
            'GET',
            '/api/v2/enterprise/coupons/{}/codes/'.format(coupon_id)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = response.json()
        assert response == ['code_filter must be specified']

    def test_coupon_codes_detail_csv(self):
        """
        Verify that `/api/v2/enterprise/coupons/{coupon_id}/codes/` endpoint returns correct csv data.
        """
        coupon_data = {
            'voucher_type': Voucher.MULTI_USE,
            'quantity': 2,
            'max_uses': 3
        }

        coupon_id = self.create_coupon_with_applications(
            self.data,
            coupon_data['voucher_type'],
            coupon_data['quantity'],
            coupon_data['max_uses']
        )

        response = self.get_response(
            'GET',
            '/api/v2/enterprise/coupons/{}/codes.csv?code_filter={}'.format(coupon_id, VOUCHER_REDEEMED)
        )
        csv_content = response.content.decode('utf-8').split('\r\n')
        csv_header = csv_content[0]
        # Strip out first row (headers) and last row (extra csv line)
        csv_data = csv_content[1:-1]

        # Verify headers.
        self.assertEqual(csv_header, 'assigned_to,code,redeem_url,redemptions.total,redemptions.used')

        # Verify csv data.
        self.assert_coupon_codes_response(
            csv_data,
            coupon_id,
            1,
            6,
            is_csv=True
        )

    @ddt.data(
        {
            'page': 1,
            'page_size': 2,
            'pagination': {
                'count': 6,
                'current_page': 1,
                'num_pages': 3,
                'next': 'http://testserver.fake/api/v2/enterprise/coupons/{}/codes/?code_filter={}&page=2&page_size=2',
                'previous': None,
            },
            'expected_results_count': 2,
        },
        {
            'page': 2,
            'page_size': 4,
            'pagination': {
                'count': 6,
                'current_page': 2,
                'num_pages': 2,
                'next': None,
                'previous': 'http://testserver.fake/api/v2/enterprise/coupons/{}/codes/?code_filter={}&page_size=4',
            },
            'expected_results_count': 2,
        },
        {
            'page': 2,
            'page_size': 3,
            'pagination': {
                'count': 6,
                'current_page': 2,
                'num_pages': 2,
                'next': None,
                'previous': 'http://testserver.fake/api/v2/enterprise/coupons/{}/codes/?code_filter={}&page_size=3',
            },
            'expected_results_count': 3,
        },
    )
    @ddt.unpack
    def test_coupon_codes_detail_with_pagination(self, page, page_size, pagination, expected_results_count):
        """
        Verify that `/api/v2/enterprise/coupons/{coupon_id}/codes/` endpoint pagination works
        """
        coupon_data = {
            'voucher_type': Voucher.MULTI_USE,
            'quantity': 2,
            'max_uses': 3,
        }

        coupon_id = self.create_coupon_with_applications(
            self.data,
            coupon_data['voucher_type'],
            coupon_data['quantity'],
            coupon_data['max_uses']
        )

        # update the coupon id in `previous` and next urls
        pagination['previous'] = pagination['previous'] and pagination['previous'].format(coupon_id, VOUCHER_REDEEMED)
        pagination['next'] = pagination['next'] and pagination['next'].format(coupon_id, VOUCHER_REDEEMED)

        endpoint = '/api/v2/enterprise/coupons/{}/codes/?code_filter={}&page={}&page_size={}'.format(
            coupon_id, VOUCHER_REDEEMED, page, page_size
        )

        # get coupon codes usage details
        response = self.get_response('GET', endpoint)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = response.json()
        self.assert_coupon_codes_response(
            response,
            coupon_id,
            1,
            expected_results_count,
            pagination=pagination,
        )

    def test_unredeemed_filter_email_bounced_codes(self):
        """
        Test that codes with `OFFER_ASSIGNMENT_EMAIL_BOUNCED` error status are shown in unredeemed filter.
        """
        coupon_response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        coupon = coupon_response.json()
        coupon_id = coupon['coupon_id']
        vouchers = Product.objects.get(id=coupon_id).attr.coupon_vouchers.vouchers.all()
        codes = [voucher.code for voucher in vouchers]

        # Code assignments.
        self.assign_user_to_code(coupon_id, ['user1@example.com'], [codes[0]])

        response = self.get_response(
            'GET',
            '/api/v2/enterprise/coupons/{}/codes/?code_filter={}'.format(coupon_id, VOUCHER_NOT_REDEEMED)
        ).json()

        # Verify that code appears in unredeemed filter.
        self.assert_code_detail_response(
            response['results'],
            [{'code': 0, 'assigned_to': 'user1@example.com', 'redemptions': {'used': 0, 'total': 1}}],
            codes
        )

        # Email bounce a code.
        OfferAssignment.objects.filter(code=vouchers[0].code).update(status=OFFER_ASSIGNMENT_EMAIL_BOUNCED)

        response = self.get_response(
            'GET',
            '/api/v2/enterprise/coupons/{}/codes/?code_filter={}'.format(coupon_id, VOUCHER_NOT_REDEEMED)
        ).json()

        # Now verify that code still appears in unredeemed filter.
        self.assert_code_detail_response(
            response['results'],
            [{'code': 0, 'assigned_to': 'user1@example.com', 'redemptions': {'used': 0, 'total': 1}}],
            codes
        )

    def test_implicit_permission_coupon_overview(self):
        """
        Test that we get implicit access via role assignment
        """
        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        EcommerceFeatureRoleAssignment.objects.all().delete()
        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_implicit_permission_codes_detail(self):
        """
        Test that we get access when basket and invoice are present
        """
        self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        coupon = Product.objects.get(title=self.data['title'])
        EcommerceFeatureRoleAssignment.objects.all().delete()
        response = self.get_response(
            'GET',
            '/api/v2/enterprise/coupons/{}/codes/?code_filter={}'.format(coupon.id, VOUCHER_NOT_ASSIGNED)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_permission_search_404(self):
        """
        Test that we get a 404 if no email is handed to us
        """
        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/search-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            )
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_permission_search_403(self):
        """
        Test that we get implicit access via role assignment
        """
        self.set_jwt_cookie(
            system_wide_role='incorrect-role', context=self.data['enterprise_customer']['id']
        )
        EcommerceFeatureRoleAssignment.objects.all().delete()
        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/search-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            )
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_search_user_does_not_exist(self):
        """
        Test that 200 with empty results is returned if we cant find the user
        """
        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/search-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            ),
            data={'user_email': 'iamsofake@notreal.com'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assert response.json()['results'] == []

    def test_search_code_does_not_exist(self):
        """
        Test that 200 with empty results is returned if we cant find the code
        """
        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/search-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            ),
            data={'voucher_code': '3456QWTERF46PS1R'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assert response.json()['results'] == []

    def test_search_user_does_not_exist_but_has_offer_assignment(self):
        """
        Test that 200 with populated results is returned if we cant find the user,
        but an offerAssignment exists with the user email specified
        """
        coupon1 = self.create_coupon(
            benefit_type=Benefit.PERCENTAGE,
            benefit_value=40,
            enterprise_customer=self.data['enterprise_customer']['id'],
            enterprise_customer_catalog='aaaaaaaa-2c44-487b-9b6a-24eee973f9a4',
            code='AAAAA',
        )
        voucher1 = coupon1.coupon_vouchers.first().vouchers.first()
        self.assign_user_to_code(coupon1.id, ['iHaveNoUser@object.com'], ['AAAAA'])

        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/search-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            ),
            data={'user_email': 'iHaveNoUser@object.com'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.json()['results']
        assert len(results) == 1
        assert results[0]['voucher_id'] == voucher1.id
        assert results[0]['code'] == voucher1.code
        assert results[0]['course_key'] is None

    def test_search_code_with_offer_assignment(self):
        """
        Test that 200 with populated results is returned if search is made by code,
        and code has offer_assignment
        """
        coupon1 = self.create_coupon(
            benefit_type=Benefit.PERCENTAGE,
            benefit_value=40,
            enterprise_customer=self.data['enterprise_customer']['id'],
            enterprise_customer_catalog='aaaaaaaa-2c44-487b-9b6a-24eee973f9a4',
            code='ABCDEFGH1234567',
        )
        voucher1 = coupon1.coupon_vouchers.first().vouchers.first()
        self.assign_user_to_code(coupon1.id, ['iHaveNoUser@object.com'], ['ABCDEFGH1234567'])

        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/search-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            ),
            data={'voucher_code': 'ABCDEFGH1234567'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.json()['results']
        assert len(results) == 1
        assert results[0]['voucher_id'] == voucher1.id
        assert results[0]['code'] == voucher1.code
        assert results[0]['course_key'] is None

    def test_search_code_without_offer_assignment(self):
        """
        Test that 200 with populated results is returned if an unassigned code is searched
        """
        coupon1 = self.create_coupon(
            benefit_type=Benefit.PERCENTAGE,
            benefit_value=40,
            enterprise_customer=self.data['enterprise_customer']['id'],
            enterprise_customer_catalog='aaaaaaaa-2c44-487b-9b6a-24eee973f9a4',
            code='ABCDEFGH1234567',
        )
        voucher1 = coupon1.coupon_vouchers.first().vouchers.first()
        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/search-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            ),
            data={'voucher_code': 'ABCDEFGH1234567'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.json()['results']
        assert len(results) == 1
        assert results[0]['voucher_id'] == voucher1.id
        assert results[0]['code'] == voucher1.code
        assert results[0]['course_key'] is None

    def test_permission_search_200(self):
        """
        Test that we get implicit access via role assignment
        """
        # Create coupons
        coupon1 = self.create_coupon(
            benefit_type=Benefit.PERCENTAGE,
            benefit_value=40,
            enterprise_customer=self.data['enterprise_customer']['id'],
            enterprise_customer_catalog='aaaaaaaa-2c44-487b-9b6a-24eee973f9a4',
            code='AAAAA',
        )
        coupon2 = self.create_coupon(
            max_uses=2,
            voucher_type=Voucher.MULTI_USE,
            benefit_type=Benefit.FIXED,
            benefit_value=13.37,
            enterprise_customer=self.data['enterprise_customer']['id'],
            enterprise_customer_catalog='bbbbbbbb-2c44-487b-9b6a-24eee973f9a4',
        )
        coupon3 = self.create_coupon(
            max_uses=2,
            voucher_type=Voucher.MULTI_USE,
            benefit_type=Benefit.FIXED,
            benefit_value=12345,
            enterprise_customer=self.data['enterprise_customer']['id'],
            enterprise_customer_catalog='dddddddd-2c44-487b-9b6a-24eee973f9a4',
        )
        coupon_with_other_enterprise = self.create_coupon(
            max_uses=7,
            voucher_type=Voucher.MULTI_USE_PER_CUSTOMER,
            benefit_type=Benefit.FIXED,
            benefit_value=444,
            enterprise_customer='cccccccc-cccc-cccc-cccc-24eee973f9a4',
            enterprise_customer_catalog='cccccccc-2c44-487b-9b6a-24eee973f9a4',
        )
        # Assign codes using the assignment endpoint
        self.assign_user_to_code(coupon1.id, [self.user.email], ['AAAAA'])
        self.assign_user_to_code(coupon2.id, [self.user.email], [])
        self.assign_user_to_code(coupon2.id, [self.user.email], [])
        self.assign_user_to_code(coupon_with_other_enterprise.id, [self.user.email], [])

        # Redeem a voucher without using the assignment endpoint
        self.use_voucher(coupon3.coupon_vouchers.first().vouchers.first(), self.user)

        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/search-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            ),
            data={'user_email': self.user.email}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.json()['results']
        voucher1 = coupon1.coupon_vouchers.first().vouchers.first()
        voucher2 = coupon2.coupon_vouchers.first().vouchers.first()
        voucher3 = coupon3.coupon_vouchers.first().vouchers.first()

        # assert counts are right
        assign_redeem_counts = Counter(item['coupon_id'] for item in results)
        assert assign_redeem_counts[coupon1.id] == 1
        assert assign_redeem_counts[coupon2.id] == 2
        assert assign_redeem_counts[coupon3.id] == 1

        # assert values for each are right
        for item in results:
            if item['voucher_id'] == voucher1.id:
                assert item['coupon_id'] == coupon1.id
                assert item['coupon_name'] == coupon1.title
                assert item['code'] == voucher1.code
                assert item['course_key'] is None
                assert item['course_title'] is None
            elif item['voucher_id'] == voucher2.id:
                assert item['coupon_id'] == coupon2.id
                assert item['coupon_name'] == coupon2.title
                assert item['code'] == voucher2.code
                assert item['course_key'] is None
                assert item['course_title'] is None
            elif item['voucher_id'] == voucher3.id:
                assert item['coupon_id'] == coupon3.id
                assert item['coupon_name'] == coupon3.title
                assert item['code'] == voucher3.code
                course = coupon3.coupon_vouchers.first().vouchers.first(
                ).applications.first().order.lines.first().product.course
                assert item['course_key'] == course.id
                assert item['course_title'] == course.name
            else:
                assert False

    def test_implicit_permission_incorrect_role(self):
        """
        Test that we get implicit access via role assignment
        """
        self.set_jwt_cookie(
            system_wide_role='incorrect-role', context=self.data['enterprise_customer']['id']
        )
        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        EcommerceFeatureRoleAssignment.objects.all().delete()
        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            )
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(SYSTEM_TO_FEATURE_ROLE_MAPPING={SYSTEM_ENTERPRISE_ADMIN_ROLE: ['dummy-role']})
    def test_explicit_permission_coupon_overview(self):
        """
        Test that we get explicit access via role assignment
        """
        # Re-order rules predicate to check explicit access first.
        rules.remove_perm('enterprise.can_view_coupon')
        rules.add_perm(
            'enterprise.can_view_coupon',
            request_user_has_explicit_access |
            request_user_has_implicit_access
        )

        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @override_settings(SYSTEM_TO_FEATURE_ROLE_MAPPING={SYSTEM_ENTERPRISE_ADMIN_ROLE: ['dummy-role']})
    def test_explicit_permission_denied_coupon_overview(self):
        """
        Test that we get access denied via role assignment when no assignment exists
        """
        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        EcommerceFeatureRoleAssignment.objects.all().delete()
        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            )
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(SYSTEM_TO_FEATURE_ROLE_MAPPING={SYSTEM_ENTERPRISE_ADMIN_ROLE: ['dummy-role']})
    def test_explicit_permission_denied_no_feature_role_assignment(self):
        """
        Test that we get access denied when role assignment with enterprise_id is absent
        """
        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        EcommerceFeatureRoleAssignment.objects.filter(
            user=self.user,
            enterprise_id=self.data['enterprise_customer']['id']
        ).delete()
        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            )
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_permissions_with_enterprise_openedx_operator(self):
        """
        Test that role base permissions works as expected with `enterprise_openedx_operator` role.
        """
        self.set_jwt_cookie(system_wide_role=SYSTEM_ENTERPRISE_OPERATOR_ROLE, context=ALL_ACCESS_CONTEXT)

        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        EcommerceFeatureRoleAssignment.objects.all().delete()

        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @ddt.data(SYSTEM_ENTERPRISE_ADMIN_ROLE, 'role_with_no_mapped_permissions')
    def test_permissions_with_all_access_context(self, system_wide_role):
        """
        Test that role base permissions works as expected with all access context.
        """
        # Create a feature role assignment with no enterprise id i.e it would have all access context.
        EcommerceFeatureRoleAssignment.objects.all().delete()
        EcommerceFeatureRoleAssignment.objects.get_or_create(
            role=self.role,
            user=self.user
        )

        self.set_jwt_cookie(
            system_wide_role=system_wide_role, context=ALL_ACCESS_CONTEXT
        )

        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, self.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.get_response(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
                kwargs={'enterprise_id': self.data['enterprise_customer']['id']}
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @ddt.data(
        (
            '85b08dde-0877-4474-a4e9-8408fe47ce88',
            ['coupon-1', 'coupon-2']
        ),
        (
            'f5c9149f-8dce-4410-bb0f-85c0f2dda864',
            ['coupon-3']
        ),
        (
            'f5c9149f-8dce-4410-bb0f-85c0f2dda860',
            []
        ),
    )
    @ddt.unpack
    def test_get_enterprise_coupon_overview_data(self, enterprise_id, expected_coupons):
        """
        Test if we get correct enterprise coupon overview data.
        """
        coupons_data = [{
            'title': 'coupon-1',
            'enterprise_customer': {'name': 'LOTRx', 'id': '85b08dde-0877-4474-a4e9-8408fe47ce88'}
        }, {
            'title': 'coupon-2',
            'enterprise_customer': {'name': 'LOTRx', 'id': '85b08dde-0877-4474-a4e9-8408fe47ce88'}
        }, {
            'title': 'coupon-3',
            'enterprise_customer': {'name': 'HPx', 'id': 'f5c9149f-8dce-4410-bb0f-85c0f2dda864'}
        }]
        EcommerceFeatureRoleAssignment.objects.all().delete()
        EcommerceFeatureRoleAssignment.objects.get_or_create(
            role=self.role,
            user=self.user,
            enterprise_id=enterprise_id
        )
        # Create coupons.
        for coupon_data in coupons_data:
            self.get_response('POST', ENTERPRISE_COUPONS_LINK, dict(self.data, **coupon_data))

        # Build expected results.
        expected_results = []
        for coupon_title in expected_coupons:
            expected_results.append(self.get_coupon_data(coupon_title))

        overview_response = self.get_response_json(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
                kwargs={'enterprise_id': enterprise_id}
            )
        )

        # Verify that we get correct number of results related enterprise id.
        self.assertEqual(overview_response['count'], len(expected_results))

        # Verify that we get correct results.
        for actual_result in overview_response['results']:
            self.assertIn(actual_result, expected_results)

    def test_get_enterprise_coupon_overview_data_with_active_filter(self):
        """
        Test if we get correct enterprise coupon overview data with some inactive coupons.
        """
        enterprise_id = '85b08dde-0877-4474-a4e9-8408fe47ce88'
        EcommerceFeatureRoleAssignment.objects.all().delete()
        EcommerceFeatureRoleAssignment.objects.get_or_create(
            role=self.role,
            user=self.user,
            enterprise_id=enterprise_id
        )

        active_coupon_titles = ['coupon-1', 'coupon-2', 'coupon-3']
        inactive_coupon_titles = ['coupon-4', 'coupon-5']

        # Create coupons.
        for coupon_title in active_coupon_titles + inactive_coupon_titles:
            data = dict(
                self.data,
                title=coupon_title,
                enterprise_customer={'name': 'LOTRx', 'id': enterprise_id}
            )
            self.get_response('POST', ENTERPRISE_COUPONS_LINK, data)

        # now set coupon inactive
        for inactive_coupon_title in inactive_coupon_titles:
            inactive_coupon = Product.objects.get(title=inactive_coupon_title)
            inactive_coupon.attr.inactive = True
            inactive_coupon.save()

        overview_response = self.get_response_json(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
                kwargs={'enterprise_id': enterprise_id}
            )
        )

        # Build expected results.
        expected_results = []
        for coupon_title in active_coupon_titles + inactive_coupon_titles:
            expected_results.append(self.get_coupon_data(coupon_title))

        # Verify that we get correct results.
        self.assertEqual(overview_response['count'], len(expected_results))
        for actual_result in overview_response['results']:
            self.assertIn(actual_result, expected_results)

        overview_response = self.get_response_json(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
                kwargs={'enterprise_id': enterprise_id},
            ),
            data={'filter': 'active'}
        )
        # Build expected results.
        expected_results = [result for result in expected_results if result['title'] in active_coupon_titles]
        # Verify that we get correct results.
        self.assertEqual(overview_response['count'], len(expected_results))
        for actual_result in overview_response['results']:
            self.assertIn(actual_result, expected_results)

    @ddt.data(
        (
            '85b08dde-0877-4474-a4e9-8408fe47ce88',
            ['coupon-1', 'coupon-2']
        ),
        (
            'f5c9149f-8dce-4410-bb0f-85c0f2dda864',
            ['coupon-3']
        ),
    )
    @ddt.unpack
    def test_get_single_enterprise_coupon_overview_data(self, enterprise_id, expected_coupons):
        """
        Test if we get correct enterprise coupon overview data for a single coupon.
        """
        coupons_data = [{
            'title': 'coupon-1',
            'enterprise_customer': {'name': 'LOTRx', 'id': '85b08dde-0877-4474-a4e9-8408fe47ce88'}
        }, {
            'title': 'coupon-2',
            'enterprise_customer': {'name': 'LOTRx', 'id': '85b08dde-0877-4474-a4e9-8408fe47ce88'}
        }, {
            'title': 'coupon-3',
            'enterprise_customer': {'name': 'HPx', 'id': 'f5c9149f-8dce-4410-bb0f-85c0f2dda864'}
        }]
        EcommerceFeatureRoleAssignment.objects.all().delete()
        EcommerceFeatureRoleAssignment.objects.get_or_create(
            role=self.role,
            user=self.user,
            enterprise_id=enterprise_id
        )
        # Create coupons.
        for coupon_data in coupons_data:
            self.get_response('POST', ENTERPRISE_COUPONS_LINK, dict(self.data, **coupon_data))

        # Build expected results for all coupons.
        expected_results = []
        for coupon_title in expected_coupons:
            expected_results.append(self.get_coupon_data(coupon_title))

        # Build request URL with `coupon_id` query parameter
        base_url = reverse(
            'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
            kwargs={'enterprise_id': enterprise_id}
        )
        coupon_id = expected_results[0].get('id')
        request_url = '{}?{}'.format(base_url, urlencode({'coupon_id': coupon_id}))

        # Fetch single coupon overview data
        overview_response = self.get_response_json('GET', request_url)

        self.assertEqual(overview_response, expected_results[0])

    @ddt.data(
        {
            'voucher_type': Voucher.SINGLE_USE,
            'quantity': 3,
            'max_uses': None,
            'code_assignments': [0, 0, 0],
            'code_redemptions': [0, 0, 0],
            'expected_response': {'max_uses': 3, 'num_codes': 3, 'num_unassigned': 3, 'num_uses': 0, 'errors': []}
        },
        {
            'voucher_type': Voucher.SINGLE_USE,
            'quantity': 3,
            'max_uses': None,
            'code_assignments': [1, 0, 0],
            'code_redemptions': [0, 1, 0],
            'expected_response': {'max_uses': 3, 'num_codes': 3, 'num_unassigned': 1, 'num_uses': 1, 'errors': []}
        },
        {
            'voucher_type': Voucher.SINGLE_USE,
            'quantity': 3,
            'max_uses': None,
            'code_assignments': [1, 1, 1],
            'code_redemptions': [0, 1, 1],
            'assignment_has_error': True,
            'expected_response': {'max_uses': 3, 'num_codes': 3, 'num_unassigned': 0, 'num_uses': 2, 'errors': True}
        },
        {
            'voucher_type': Voucher.MULTI_USE_PER_CUSTOMER,
            'quantity': 3,
            'max_uses': 2,
            'code_assignments': [0, 0, 0],
            'code_redemptions': [0, 0, 0],
            'expected_response': {'max_uses': 6, 'num_codes': 3, 'num_unassigned': 6, 'num_uses': 0, 'errors': []}
        },
        {
            'voucher_type': Voucher.MULTI_USE_PER_CUSTOMER,
            'quantity': 3,
            'max_uses': 2,
            'code_assignments': [1, 0, 0],
            'code_redemptions': [0, 1, 0],
            'expected_response': {'max_uses': 6, 'num_codes': 3, 'num_unassigned': 2, 'num_uses': 1, 'errors': []}
        },
        {
            'voucher_type': Voucher.MULTI_USE_PER_CUSTOMER,
            'quantity': 3,
            'max_uses': 2,
            'code_assignments': [1, 1, 0],
            'code_redemptions': [0, 2, 0],
            'expected_response': {'max_uses': 6, 'num_codes': 3, 'num_unassigned': 2, 'num_uses': 2, 'errors': []}
        },
        {
            'voucher_type': Voucher.MULTI_USE_PER_CUSTOMER,
            'quantity': 3,
            'max_uses': 2,
            'code_assignments': [1, 1, 1],
            'code_redemptions': [0, 1, 2],
            'assignment_has_error': True,
            'expected_response': {'max_uses': 6, 'num_codes': 3, 'num_unassigned': 0, 'num_uses': 3, 'errors': True}
        },
        {
            'voucher_type': Voucher.MULTI_USE,
            'quantity': 3,
            'max_uses': 2,
            'code_assignments': [1, 0, 0],
            'code_redemptions': [0, 1, 0],
            'expected_response': {'max_uses': 6, 'num_codes': 3, 'num_unassigned': 4, 'num_uses': 1, 'errors': []}
        },
        {
            'voucher_type': Voucher.MULTI_USE,
            'quantity': 3,
            'max_uses': 2,
            'code_assignments': [2, 0, 0],
            'code_redemptions': [0, 2, 0],
            'expected_response': {'max_uses': 6, 'num_codes': 3, 'num_unassigned': 2, 'num_uses': 2, 'errors': []}
        },
        {
            'voucher_type': Voucher.MULTI_USE,
            'quantity': 3,
            'max_uses': 2,
            'code_assignments': [2, 1, 1],
            'code_redemptions': [2, 0, 1],
            'expected_response': {'max_uses': 6, 'num_codes': 3, 'num_unassigned': 1, 'num_uses': 3, 'errors': []}
        },
        {
            'voucher_type': Voucher.MULTI_USE,
            'quantity': 3,
            'max_uses': 2,
            'code_assignments': [2, 2, 2],
            'code_redemptions': [0, 0, 0],
            'expected_response': {'max_uses': 6, 'num_codes': 3, 'num_unassigned': 0, 'num_uses': 0, 'errors': []}
        },
        {
            'voucher_type': Voucher.MULTI_USE,
            'quantity': 1,
            'max_uses': None,
            'code_assignments': [1],
            'code_redemptions': [1],
            'assignment_has_error': True,
            'expected_response': {
                'max_uses': 10000, 'num_codes': 1, 'num_unassigned': 9998, 'num_uses': 1, 'errors': True
            }
        },
        {
            'voucher_type': Voucher.ONCE_PER_CUSTOMER,
            'quantity': 3,
            'max_uses': 2,
            'code_assignments': [0, 0, 0],
            'code_redemptions': [0, 0, 0],
            'expected_response': {'max_uses': 6, 'num_codes': 3, 'num_unassigned': 6, 'num_uses': 0, 'errors': []}
        },
        {
            'voucher_type': Voucher.ONCE_PER_CUSTOMER,
            'quantity': 3,
            'max_uses': 2,
            'code_assignments': [2, 1, 0],
            'code_redemptions': [0, 0, 1],
            'assignment_has_error': True,
            'expected_response': {'max_uses': 6, 'num_codes': 3, 'num_unassigned': 2, 'num_uses': 1, 'errors': True}
        },
    )
    @ddt.unpack
    def test_coupon_overview_fields(
            self,
            voucher_type,
            quantity,
            max_uses,
            code_assignments,
            code_redemptions,
            expected_response,
            assignment_has_error=False):
        """
        Tests coupon overview endpoint returns correct value for calculated fields.
        """
        enterprise_id = '85b08dde-0877-4474-a4e9-8408fe47ce88'
        coupon_data = {
            'max_uses': max_uses,
            'quantity': quantity,
            'voucher_type': voucher_type,
            'enterprise_customer': {'name': 'LOTRx', 'id': enterprise_id}
        }
        EcommerceFeatureRoleAssignment.objects.all().delete()
        EcommerceFeatureRoleAssignment.objects.get_or_create(
            role=self.role,
            user=self.user,
            enterprise_id=enterprise_id
        )
        coupon_response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, dict(self.data, **coupon_data))
        coupon = coupon_response.json()
        coupon_id = coupon['coupon_id']
        vouchers = Product.objects.get(id=coupon_id).attr.coupon_vouchers.vouchers.all()

        # code assignments.
        self.assign_coupon_codes(coupon_id, vouchers, code_assignments)

        # Should we update assignment with error
        if assignment_has_error:
            assignment = OfferAssignment.objects.filter(code=vouchers[0].code).first()
            if assignment:
                assignment.status = OFFER_ASSIGNMENT_EMAIL_BOUNCED
                assignment.save()

        for i, voucher in enumerate(vouchers):
            for _ in range(0, code_redemptions[i]):
                self.use_voucher(voucher, self.create_user())

        coupon_overview_response = self.get_response_json(
            'GET',
            reverse(
                'api:v2:enterprise-coupons-(?P<enterprise-id>.+)/overview-list',
                kwargs={'enterprise_id': enterprise_id}
            )
        )

        # Verify that we get correct results.
        for field, value in six.iteritems(expected_response):
            if assignment_has_error and field == 'errors':
                assignment_with_errors = OfferAssignment.objects.filter(status=OFFER_ASSIGNMENT_EMAIL_BOUNCED)
                value = [
                    {
                        'id': assignment.id,
                        'code': assignment.code,
                        'user_email': assignment.user_email
                    }
                    for assignment in assignment_with_errors
                ]
            self.assertEqual(coupon_overview_response['results'][0][field], value)

    @ddt.data(
        (Voucher.SINGLE_USE, 2, None, ['test1@example.com', 'test2@example.com'], [1]),
        (Voucher.MULTI_USE_PER_CUSTOMER, 2, 3, ['test1@example.com', 'test2@example.com'], [3]),
        (Voucher.MULTI_USE, 1, None, ['test1@example.com', 'test2@example.com'], [2]),
        (Voucher.MULTI_USE, 2, 3, ['t1@example.com', 't2@example.com', 't3@example.com', 't4@example.com'], [3, 1]),
        (Voucher.ONCE_PER_CUSTOMER, 2, 2, ['test1@example.com', 'test2@example.com'], [2]),
    )
    @ddt.unpack
    def test_coupon_codes_assign_success(self, voucher_type, quantity, max_uses, emails, assignments_per_code):
        """Test assigning codes to users."""
        coupon_post_data = dict(self.data, voucher_type=voucher_type, quantity=quantity, max_uses=max_uses)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': emails
                }
            )
        response = response.json()
        assert mock_send_email.call_count == len(emails)
        for i, email in enumerate(emails):
            if voucher_type != Voucher.MULTI_USE_PER_CUSTOMER:
                assert response['offer_assignments'][i]['user_email'] == email
            else:
                for j in range(max_uses):
                    assert response['offer_assignments'][(i * max_uses) + j]['user_email'] == email

        assigned_codes = []
        for assignment in response['offer_assignments']:
            if assignment['code'] not in assigned_codes:
                assigned_codes.append(assignment['code'])

        for code in assigned_codes:
            assert OfferAssignment.objects.filter(code=code).count() in assignments_per_code

    def test_coupon_codes_assign_success_with_codes_filter(self):
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=5)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']

        vouchers = Product.objects.get(id=coupon_id).attr.coupon_vouchers.vouchers.all()
        codes = [voucher.code for voucher in vouchers]
        codes_param = codes[3:]

        emails = ['t1@example.com', 't2@example.com']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': emails,
                    'codes': codes_param
                }
            )
        response = response.json()
        assert mock_send_email.call_count == len(emails)
        for i, email in enumerate(emails):
            assert response['offer_assignments'][i]['user_email'] == email
            assert response['offer_assignments'][i]['code'] in codes_param

        for code in codes:
            if code not in codes_param:
                assert OfferAssignment.objects.filter(code=code).count() == 0
            else:
                assert OfferAssignment.objects.filter(code=code).count() == 1

    def test_coupon_codes_assign_success_exclude_used_codes(self):
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=5)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']

        vouchers = Product.objects.get(id=coupon_id).attr.coupon_vouchers.vouchers.all()
        # Use some of the vouchers
        used_codes = []
        for voucher in vouchers[:3]:
            self.use_voucher(voucher, self.create_user())
            used_codes.append(voucher.code)
        unused_codes = [voucher.code for voucher in vouchers[3:]]
        emails = ['t1@example.com', 't2@example.com']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': emails
                }
            )
        response = response.json()
        assert mock_send_email.call_count == len(emails)
        for i, email in enumerate(emails):
            assert response['offer_assignments'][i]['user_email'] == email
            assert response['offer_assignments'][i]['code'] in unused_codes

        for code in used_codes:
            assert OfferAssignment.objects.filter(code=code).count() == 0
        for code in unused_codes:
            assert OfferAssignment.objects.filter(code=code).count() == 1

    def test_coupon_codes_assign_once_per_customer_with_used_codes(self):
        coupon_post_data = dict(self.data, voucher_type=Voucher.ONCE_PER_CUSTOMER, quantity=3)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']

        vouchers = Product.objects.get(id=coupon_id).attr.coupon_vouchers.vouchers.all()
        # Redeem and assign two of the vouchers
        already_redeemed_voucher = vouchers[0]
        already_assigned_voucher = vouchers[1]
        unused_voucher = vouchers[2]
        redeemed_user = self.create_user(email='t1@example.com')
        self.use_voucher(already_redeemed_voucher, redeemed_user)
        OfferAssignment.objects.create(
            code=already_assigned_voucher.code,
            offer=already_assigned_voucher.enterprise_offer,
            user_email='t2@example.com',
        )
        emails = ['t1@example.com', 't2@example.com', 't3@example.com']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': emails
                }
            )
        response = response.json()
        assert mock_send_email.call_count == len(emails)
        for i, email in enumerate(emails):
            assert response['offer_assignments'][i]['user_email'] == email
            assert response['offer_assignments'][i]['code'] == unused_voucher.code

        assert OfferAssignment.objects.filter(code=unused_voucher.code).count() == 3
        assert OfferAssignment.objects.filter(code=already_assigned_voucher.code).count() == 1
        assert OfferAssignment.objects.filter(code=already_redeemed_voucher.code).count() == 0

    def test_coupon_codes_assign_once_per_customer_with_revoked_code(self):
        coupon_post_data = dict(self.data, voucher_type=Voucher.ONCE_PER_CUSTOMER, quantity=1)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']

        vouchers = Product.objects.get(id=coupon_id).attr.coupon_vouchers.vouchers.all()
        voucher = vouchers[0]
        email = 't1@example.com'
        # Assign the code to the user.
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': [email]
                }
            )
        response = response.json()
        assert mock_send_email.call_count == 1
        assert response['offer_assignments'][0]['user_email'] == email
        assert response['offer_assignments'][0]['code'] == voucher.code

        # Revoke the code from the user.
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_update_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/revoke/'.format(coupon_id),
                {'assignments': [{'email': email, 'code': voucher.code}]}
            )

        response = response.json()
        assert response == [{'code': voucher.code, 'email': email, 'detail': 'success'}]

        # Assign the same code to the user again.
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': [email]
                }
            )
        response = response.json()
        assert mock_send_email.call_count == 1
        assert response['offer_assignments'][0]['user_email'] == email
        assert response['offer_assignments'][0]['code'] == voucher.code

    @ddt.data(
        (Voucher.SINGLE_USE, 1, None, ['test1@example.com', 'test2@example.com']),
        (Voucher.MULTI_USE_PER_CUSTOMER, 1, 3, ['test1@example.com', 'test2@example.com']),
        (Voucher.MULTI_USE, 1, 3, ['t1@example.com', 't2@example.com', 't3@example.com', 't4@example.com']),
    )
    @ddt.unpack
    def test_coupon_codes_assign_failure(self, voucher_type, quantity, max_uses, emails):
        coupon_post_data = dict(self.data, voucher_type=voucher_type, quantity=quantity, max_uses=max_uses)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': emails
                }
            )
        response = response.json()
        assert response['non_field_errors'] == ['Not enough available codes for assignment!']
        assert mock_send_email.call_count == 0

    @ddt.data(
        (Voucher.SINGLE_USE, 2, None, ['test1@example.com', 'test2@example.com'], [1]),
        (Voucher.MULTI_USE_PER_CUSTOMER, 2, 3, ['test1@example.com', 'test2@example.com'], [3]),
        (Voucher.MULTI_USE, 1, None, ['test1@example.com', 'test2@example.com'], [2]),
        (Voucher.MULTI_USE, 2, 3, ['t1@example.com', 't2@example.com', 't3@example.com', 't4@example.com'], [3, 1]),
        (Voucher.ONCE_PER_CUSTOMER, 2, 2, ['test1@example.com', 'test2@example.com'], [2]),
    )
    @ddt.unpack
    def test_codes_assignment_email_failure(self, voucher_type, quantity, max_uses, emails, assignments_per_code):
        """Test assigning codes to users."""
        coupon_post_data = dict(self.data, voucher_type=voucher_type, quantity=quantity, max_uses=max_uses)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        with mock.patch(
                'ecommerce.extensions.offer.utils.send_offer_assignment_email.delay',
                side_effect=Exception()) as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': emails
                }
            )
        response = response.json()
        assert mock_send_email.call_count == len(emails)
        for i, email in enumerate(emails):
            if voucher_type != Voucher.MULTI_USE_PER_CUSTOMER:
                assert response['offer_assignments'][i]['user_email'] == email
            else:
                for j in range(max_uses):
                    assert response['offer_assignments'][(i * max_uses) + j]['user_email'] == email

        assigned_codes = []
        for assignment in response['offer_assignments']:
            if assignment['code'] not in assigned_codes:
                assigned_codes.append(assignment['code'])

        for code in assigned_codes:
            assert OfferAssignment.objects.filter(code=code).count() in assignments_per_code

    @ddt.data(
        (Voucher.SINGLE_USE, 2, None, True),
        (Voucher.MULTI_USE_PER_CUSTOMER, 2, 3, True),
        (Voucher.MULTI_USE, 1, None, True),
        (Voucher.ONCE_PER_CUSTOMER, 2, 2, True),
    )
    @ddt.unpack
    def test_coupon_codes_revoke_success(self, voucher_type, quantity, max_uses, send_email):
        """Test revoking codes from users."""
        email = 'test1@example.com'
        coupon_post_data = dict(self.data, voucher_type=voucher_type, quantity=quantity, max_uses=max_uses)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay'):
            self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': [email]
                }
            )

        offer_assignment = OfferAssignment.objects.filter(user_email=email).first()
        payload = {'assignments': [{'email': email, 'code': offer_assignment.code}]}
        if send_email:
            payload['template'] = 'Test template'
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_update_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/revoke/'.format(coupon_id),
                payload
            )

        response = response.json()
        assert response == [{'code': offer_assignment.code, 'email': email, 'detail': 'success'}]
        assert mock_send_email.call_count == (1 if send_email else 0)
        for offer_assignment in OfferAssignment.objects.filter(user_email=email):
            assert offer_assignment.status == OFFER_ASSIGNMENT_REVOKED

    def test_coupon_codes_revoke_success_with_bounced_email(self):
        """Test revoking codes from users when the offer assignment has bounced email status."""
        email = 'test1@example.com'
        coupon_post_data = dict(
            self.data,
            voucher_type=Voucher.ONCE_PER_CUSTOMER,
            quantity=1,
            max_uses=1,
        )
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay'):
            self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': [email]
                }
            )

        offer_assignment = OfferAssignment.objects.filter(user_email=email).first()
        offer_assignment.status = OFFER_ASSIGNMENT_EMAIL_BOUNCED
        offer_assignment.save()

        payload = {'assignments': [{'email': email, 'code': offer_assignment.code}]}
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_update_email.delay'):
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/revoke/'.format(coupon_id),
                payload
            )

        response = response.json()
        assert response == [{'code': offer_assignment.code, 'email': email, 'detail': 'success'}]
        for offer_assignment in OfferAssignment.objects.filter(user_email=email):
            assert offer_assignment.status == OFFER_ASSIGNMENT_REVOKED

    def test_coupon_codes_revoke_invalid_request(self):
        """Test that revoke fails when the request format is incorrect."""
        email = 'test1@example.com'
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=1)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']

        response = self.get_response(
            'POST',
            '/api/v2/enterprise/coupons/{}/revoke/'.format(coupon_id),
            {
                'template': 'Test template',
                'template_greeting': TEMPLATE_GREETING,
                'template_closing': TEMPLATE_CLOSING,
                'assignments': {'email': email, 'code': 'RANDOMCODE'}
            }
        )

        response = response.json()
        assert response == {'non_field_errors': ['Expected a list of items but got type "dict".']}

    def test_coupon_codes_revoke_code_not_in_coupon(self):
        """Test that revoke fails when the specified code is not associated with the Coupon."""
        email = 'test1@example.com'
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=1)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']

        response = self.get_response(
            'POST',
            '/api/v2/enterprise/coupons/{}/revoke/'.format(coupon_id),
            {
                'template': 'Test template',
                'template_greeting': TEMPLATE_GREETING,
                'template_closing': TEMPLATE_CLOSING,
                'assignments': [{'email': email, 'code': 'RANDOMCODE'}]
            }
        )

        response = response.json()
        assert response == [
            {
                'code': 'RANDOMCODE',
                'email': 'test1@example.com',
                'detail': 'failure',
                'message': 'Code RANDOMCODE is not associated with this Coupon',
            }
        ]

    def test_coupon_codes_revoke_no_assignment_exists(self):
        """Test that revoke fails when the user has no existing assignments for the code."""
        email = 'test1@example.com'
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=1)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']

        voucher = Product.objects.get(id=coupon_id).attr.coupon_vouchers.vouchers.first()
        response = self.get_response(
            'POST',
            '/api/v2/enterprise/coupons/{}/revoke/'.format(coupon_id),
            {
                'template': 'Test template',
                'template_greeting': TEMPLATE_GREETING,
                'template_closing': TEMPLATE_CLOSING,
                'assignments': [{'email': email, 'code': voucher.code}]
            }
        )

        response = response.json()
        assert response == [
            {
                'code': voucher.code,
                'email': email,
                'detail': 'failure',
                'message': 'No assignments exist for user {} and code {}'.format(email, voucher.code),
            }
        ]

    def test_coupon_codes_revoke_email_failure(self):
        """Test revoking a code for a user with an email failure."""
        email = 'test1@example.com'
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=1)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay'):
            self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': [email]
                }
            )

        offer_assignment = OfferAssignment.objects.filter(user_email=email).first()
        with mock.patch(
                'ecommerce.extensions.offer.utils.send_offer_update_email.delay',
                side_effect=Exception('email_dispatch_failed')) as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/revoke/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'assignments': [{'email': email, 'code': offer_assignment.code}]
                }
            )

        response = response.json()
        assert response == [{'email': email, 'code': offer_assignment.code, 'detail': 'email_dispatch_failed'}]
        assert mock_send_email.call_count == 1
        for offer_assignment in OfferAssignment.objects.filter(user_email=email):
            assert offer_assignment.status == OFFER_ASSIGNMENT_REVOKED

    def test_coupon_codes_revoke_bulk(self):
        """Test sending multiple revoke requests (bulk use case)."""
        emails = ['test1@example.com', 'test2@example.com']
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=2)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_update_email.delay'):
            self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': emails
                }
            )

        offer_assignment = OfferAssignment.objects.filter(user_email__in=emails).first()
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_update_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/revoke/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'assignments': [
                        {'email': offer_assignment.user_email, 'code': offer_assignment.code},
                        {'email': 'test3@example.com', 'code': 'RANDOMCODE'},
                    ]
                }
            )

        response = response.json()
        assert response == [
            {'email': offer_assignment.user_email, 'code': offer_assignment.code, 'detail': 'success'},
            {
                'code': 'RANDOMCODE',
                'email': 'test3@example.com',
                'detail': 'failure',
                'message': 'Code RANDOMCODE is not associated with this Coupon',
            },
        ]
        assert mock_send_email.call_count == 1
        for offer_assignment in OfferAssignment.objects.filter(user_email=offer_assignment.user_email):
            assert offer_assignment.status == OFFER_ASSIGNMENT_REVOKED

    @ddt.data(
        (Voucher.SINGLE_USE, 2, None),
        (Voucher.MULTI_USE_PER_CUSTOMER, 2, 3),
        (Voucher.MULTI_USE, 1, None),
        (Voucher.ONCE_PER_CUSTOMER, 2, 2),
    )
    @ddt.unpack
    def test_coupon_codes_remind_success(self, voucher_type, quantity, max_uses):
        """Test sending reminder emails for codes."""
        email = 'test1@example.com'
        coupon_post_data = dict(self.data, voucher_type=voucher_type, quantity=quantity, max_uses=max_uses)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay'):
            self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': [email]
                }
            )
        offer_assignment = OfferAssignment.objects.filter(user_email=email).first()
        payload = {'assignments': [{'email': email, 'code': offer_assignment.code}]}
        payload['template'] = 'Test template'
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_update_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/remind/'.format(coupon_id),
                payload
            )
        response = response.json()
        assert response == [{'code': offer_assignment.code, 'email': email, 'detail': 'success'}]
        assert mock_send_email.call_count == 1

    def test_coupon_codes_remind_code_not_in_coupon(self):
        """Test that remind fails when the specified code is not associated with the Coupon."""
        email = 'test1@example.com'
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=1)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        response = self.get_response(
            'POST',
            '/api/v2/enterprise/coupons/{}/remind/'.format(coupon_id),
            {
                'template': 'Test template',
                'template_greeting': TEMPLATE_GREETING,
                'template_closing': TEMPLATE_CLOSING,
                'assignments': [{'email': email, 'code': 'RANDOMCODE'}]
            }
        )

        response = response.json()
        assert response == [
            {
                'code': 'RANDOMCODE',
                'email': 'test1@example.com',
                'detail': 'failure',
                'message': 'Code RANDOMCODE is not associated with this Coupon',
            }
        ]

    def test_coupon_codes_remind_no_assignment_exists(self):
        """Test that remind fails when the user has no existing assignments for the code."""
        email = 'test1@example.com'
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=1)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        voucher = Product.objects.get(id=coupon_id).attr.coupon_vouchers.vouchers.first()
        response = self.get_response(
            'POST',
            '/api/v2/enterprise/coupons/{}/remind/'.format(coupon_id),
            {
                'template': 'Test template',
                'template_greeting': TEMPLATE_GREETING,
                'template_closing': TEMPLATE_CLOSING,
                'assignments': [{'email': email, 'code': voucher.code}]
            }
        )

        response = response.json()
        assert response == [
            {
                'code': voucher.code,
                'email': email,
                'detail': 'failure',
                'message': 'No assignments exist for user {} and code {}'.format(email, voucher.code),
            }
        ]

    def test_coupon_codes_remind_email_failure(self):
        """Test sending a reminder for a code with an email failure."""
        email = 'test1@example.com'
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=1)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay'):
            self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': [email]
                }
            )
        offer_assignment = OfferAssignment.objects.filter(user_email=email).first()
        with mock.patch(
                'ecommerce.extensions.offer.utils.send_offer_update_email.delay',
                side_effect=Exception('email_dispatch_failed')) as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/remind/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'assignments': [{'email': email, 'code': offer_assignment.code}]
                }
            )
        response = response.json()
        assert response == [{'email': email, 'code': offer_assignment.code, 'detail': 'email_dispatch_failed'}]
        assert mock_send_email.call_count == 1

    def test_coupon_codes_remind_bulk(self):
        """Test sending multiple remind requests (bulk use case)."""
        emails = ['test1@example.com', 'test2@example.com']
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=2)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_update_email.delay'):
            self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': emails
                }
            )
        offer_assignment = OfferAssignment.objects.filter(user_email__in=emails).first()
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_update_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/remind/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'assignments': [
                        {'email': offer_assignment.user_email, 'code': offer_assignment.code},
                        {'email': 'test3@example.com', 'code': 'RANDOMCODE'},
                    ]
                }
            )

        response = response.json()
        assert response == [
            {'email': offer_assignment.user_email, 'code': offer_assignment.code, 'detail': 'success'},
            {
                'code': 'RANDOMCODE',
                'email': 'test3@example.com',
                'detail': 'failure',
                'message': 'Code RANDOMCODE is not associated with this Coupon',
            },
        ]
        assert mock_send_email.call_count == 1

    def test_coupon_codes_remind_all_not_redeemed(self):
        """Test sending multiple remind requests (remind all not redeemed assignments use case for)."""
        emails = ['test1@example.com', 'test2@example.com']
        coupon_post_data = dict(self.data, voucher_type=Voucher.MULTI_USE, quantity=2, max_uses=3)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        vouchers = Product.objects.get(id=coupon_id).attr.coupon_vouchers.vouchers.all()
        codes = [voucher.code for voucher in vouchers]

        for code_index, email in enumerate(emails):
            self.assign_user_to_code(coupon_id, [email], [codes[code_index]])

        offer_assignments = OfferAssignment.objects.filter(user_email__in=emails).order_by('user_email')
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_update_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/remind/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'code_filter': VOUCHER_NOT_REDEEMED
                }
            )
        response = response.json()
        assert response == [
            {'code': offer_assignment.code, 'email': offer_assignment.user_email, 'detail': 'success'}
            for offer_assignment in offer_assignments
        ]
        assert mock_send_email.call_count == 2

    def test_coupon_codes_remind_all_partial_redeemed(self):
        """Test sending multiple remind requests (remind all partial redeemed assignments use case)."""
        emails = ['test1@example.com', 'test2@example.com']
        coupon_post_data = dict(self.data, voucher_type=Voucher.MULTI_USE, quantity=2, max_uses=3)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']
        vouchers = Product.objects.get(id=coupon_id).attr.coupon_vouchers.vouchers.all()
        codes = [voucher.code for voucher in vouchers]

        for code_index, email in enumerate(emails):
            self.assign_user_to_code(coupon_id, [email], [codes[code_index]])

        # Redeem voucher partially
        redeeming_user = self.create_user(email=emails[0])
        self.use_voucher(Voucher.objects.get(code=codes[0]), redeeming_user)

        offer_assignments = OfferAssignment.objects.filter(user_email__in=[redeeming_user.email]).order_by('user_email')

        with mock.patch('ecommerce.extensions.offer.utils.send_offer_update_email.delay') as mock_send_email:
            response = self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/remind/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'code_filter': VOUCHER_PARTIAL_REDEEMED
                }
            )
        response = response.json()
        assert response == [
            {'code': offer_assignment.code, 'email': offer_assignment.user_email, 'detail': 'success'}
            for offer_assignment in offer_assignments
        ]
        assert mock_send_email.call_count == 1

    def test_coupon_codes_remind_all_with_no_code_filter(self):
        """Test sending multiple remind requests (remind all use case with no code filter supplied)."""
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=1, max_uses=None)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']

        response = self.get_response(
            'POST',
            '/api/v2/enterprise/coupons/{}/remind/'.format(coupon_id),
            {
                'template': 'Test template',
                'template_greeting': TEMPLATE_GREETING,
                'template_closing': TEMPLATE_CLOSING,
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = response.json()
        assert response == ['code_filter must be specified']

    def test_coupon_codes_remind_all_with_invalid_code_filter(self):
        """Test sending multiple remind requests (remind all use case with invalid code filter supplied)."""
        coupon_post_data = dict(self.data, voucher_type=Voucher.SINGLE_USE, quantity=1, max_uses=None)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon = coupon.json()
        coupon_id = coupon['coupon_id']

        response = self.get_response(
            'POST',
            '/api/v2/enterprise/coupons/{}/remind/'.format(coupon_id),
            {
                'template': 'Test template',
                'template_greeting': TEMPLATE_GREETING,
                'template_closing': TEMPLATE_CLOSING,
                'code_filter': 'invalid-filter'
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = response.json()
        assert response == ['Invalid code_filter specified: invalid-filter']

    @ddt.data(
        {
            'action': 'assign',
            'error': 'Coupon is not available for code assignment'
        },
        {
            'action': 'revoke',
            'error': 'Coupon is not available for code revoke'
        },
        {
            'action': 'remind',
            'error': 'Coupon is not available for code remind'
        },
    )
    @ddt.unpack
    @mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay', mock.Mock(return_value=None))
    def test_unavailable_coupon_code_actions(self, action, error):
        """
        Test `Assign/Remind/Revoke` codes from an unavailable coupon returns expected error reponse.
        """
        start_datetime = self.data['start_datetime']
        coupon_post_data = dict(self.data, end_datetime=start_datetime)
        coupon = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_post_data)
        coupon_id = coupon.json()['coupon_id']

        response = self.get_response(
            'POST',
            '/api/v2/enterprise/coupons/{}/{action}/'.format(coupon_id, action=action),
            {
                'template': 'Test template',
                'template_greeting': TEMPLATE_GREETING,
                'template_closing': TEMPLATE_CLOSING,
                'emails': ['test@edx.org']
            }
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {'error': error}

    def test_update_multi_user_per_customer_coupon(self):
        """
        Test that correct offer assignments were created when a `MULTI_USE_PER_CUSTOMER` coupon is updated.
        """
        assert OfferAssignment.objects.count() == 0

        coupon_data = dict(self.data, **{'voucher_type': Voucher.MULTI_USE_PER_CUSTOMER, 'max_uses': 1})
        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_data)

        # verify that no offer assignment should be created
        assert OfferAssignment.objects.count() == 0

        coupon = Product.objects.get(id=response.json()['coupon_id'])
        voucher1, voucher2 = coupon.attr.coupon_vouchers.vouchers.all()

        # create offer assignment for voucher1 only
        OfferAssignment.objects.create(
            code=voucher1.code,
            offer=voucher1.enterprise_offer,
            user_email='v1@example.com',
        )

        # update coupon with new max_uses, this will update the assignments for `MULTI_USE_PER_CUSTOMER` coupon only
        new_max_uses = 5
        self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'max_uses': new_max_uses
            }
        )

        # verify new assignment were created for voucher1
        voucher1_assignments = OfferAssignment.objects.filter(
            code=voucher1.code,
            offer=voucher1.enterprise_offer,
            user_email='v1@example.com',
        ).count()
        assert voucher1_assignments == new_max_uses

        # verify no assignments were created for voucher2
        voucher2_assignments = OfferAssignment.objects.filter(
            code=voucher2.code,
        ).count()
        assert voucher2_assignments == 0

    @ddt.data(
        Voucher.SINGLE_USE,
        Voucher.MULTI_USE,
        Voucher.ONCE_PER_CUSTOMER,
    )
    def test_update_non_multi_user_per_customer_coupon(self, voucher_type):
        """
        Test that no offer assignments were created when a non `MULTI_USE_PER_CUSTOMER` coupon is updated.
        """
        assert OfferAssignment.objects.count() == 0

        max_uses = None if voucher_type == Voucher.SINGLE_USE else 1
        coupon_data = dict(self.data, **{'voucher_type': voucher_type, 'max_uses': max_uses})
        response = self.get_response('POST', ENTERPRISE_COUPONS_LINK, coupon_data)
        coupon = Product.objects.get(id=response.json()['coupon_id'])
        vouchers = coupon.attr.coupon_vouchers.vouchers.all()

        # create 1 assignment for each voucher
        for index, voucher in enumerate(vouchers):
            OfferAssignment.objects.create(
                code=voucher.code,
                offer=voucher.enterprise_offer,
                user_email='v{}@example.com'.format(index),
            )

        # update coupon with new max_uses, this should not create assignments for non `MULTI_USE_PER_CUSTOMER` coupon
        new_max_uses = 5
        self.get_response(
            'PUT',
            reverse('api:v2:enterprise-coupons-detail', kwargs={'pk': coupon.id}),
            data={
                'max_uses': new_max_uses
            }
        )

        # verify that no assignment is created upon coupon update
        coupon = Product.objects.get(id=coupon.id)
        vouchers = coupon.attr.coupon_vouchers.vouchers.all()
        for index, voucher in enumerate(vouchers):
            assignments = OfferAssignment.objects.filter(
                code=voucher.code,
                offer=voucher.enterprise_offer,
                user_email='v{}@example.com'.format(index),
            ).count()
            assert assignments == 1


class OfferAssignmentSummaryViewSetTests(
        CouponMixin,
        DiscoveryTestMixin,
        DiscoveryMockMixin,
        JwtMixin,
        ThrottlingMixin,
        TestCase):
    """
    Test the enterprise coupon order functionality with role based access control.
    """
    def setUp(self):
        super(OfferAssignmentSummaryViewSetTests, self).setUp()
        self.user = self.create_user(is_staff=True, email='test@example.com')
        self.client.login(username=self.user.username, password=self.password)

        self.enterprise_customer = {'name': 'test enterprise', 'id': six.text_type(uuid4())}

        self.course = CourseFactory(id='course-v1:test-org+course+run', partner=self.partner)
        self.verified_seat = self.course.create_or_update_seat('verified', False, 100)
        self.enterprise_slug = 'batman'

        # Create coupons
        self.coupon1 = self.create_coupon(
            benefit_type=Benefit.PERCENTAGE,
            benefit_value=40,
            enterprise_customer=self.enterprise_customer['id'],
            enterprise_customer_catalog='aaaaaaaa-2c44-487b-9b6a-24eee973f9a4',
            code='AAAAA',
        )
        self.coupon2 = self.create_coupon(
            max_uses=2,
            voucher_type=Voucher.MULTI_USE,
            benefit_type=Benefit.FIXED,
            benefit_value=13.37,
            enterprise_customer=self.enterprise_customer['id'],
            enterprise_customer_catalog='bbbbbbbb-2c44-487b-9b6a-24eee973f9a4',
        )
        self.coupon3 = self.create_coupon(
            max_uses=7,
            voucher_type=Voucher.MULTI_USE_PER_CUSTOMER,
            benefit_type=Benefit.FIXED,
            benefit_value=444,
            enterprise_customer=self.enterprise_customer['id'],
            enterprise_customer_catalog='cccccccc-2c44-487b-9b6a-24eee973f9a4',
        )
        # Prepare permissions for hitting assignment endpoint
        self.role = EcommerceFeatureRole.objects.get(name=ENTERPRISE_COUPON_ADMIN_ROLE)
        EcommerceFeatureRoleAssignment.objects.get_or_create(
            role=self.role,
            user=self.user,
            enterprise_id=self.enterprise_customer['id']
        )
        self.set_jwt_cookie(
            system_wide_role=SYSTEM_ENTERPRISE_ADMIN_ROLE, context=self.enterprise_customer['id']
        )
        patcher = mock.patch('ecommerce.extensions.api.v2.utils.send_mail')
        self.send_mail_patcher = patcher.start()
        self.addCleanup(patcher.stop)

        # Assign codes using the assignment endpoint
        self.assign_user_to_code(self.coupon1.id, [self.user.email], ['AAAAA'])
        self.assign_user_to_code(self.coupon2.id, [self.user.email], [])
        self.assign_user_to_code(self.coupon3.id, [self.user.email], [])
        self.assign_user_to_code(self.coupon3.id, [self.user.email], [])

        # Revoke a code too, for testing the view's filter
        self.revoke_code_from_user(
            self.coupon3.id,
            self.user.email,
            self.coupon3.coupon_vouchers.first().vouchers.first().code
        )

    def get_response(self, method, path, data=None):
        """
        Helper method for sending requests and returning the response.
        """
        enterprise_id = ''
        enterprise_name = 'ToyX'
        if data and isinstance(data, dict) and data.get('enterprise_customer'):
            enterprise_id = data['enterprise_customer']['id']
            enterprise_name = data['enterprise_customer']['name']

        with mock.patch('ecommerce.extensions.voucher.utils.get_enterprise_customer') as patch1:
            with mock.patch('ecommerce.extensions.api.v2.utils.get_enterprise_customer') as patch2:
                patch1.return_value = patch2.return_value = {
                    'name': enterprise_name,
                    'enterprise_customer_uuid': enterprise_id,
                    'slug': self.enterprise_slug,
                }
                if method == 'GET':
                    return self.client.get(path)
                if method == 'POST':
                    return self.client.post(path, json.dumps(data), 'application/json')
                if method == 'PUT':
                    return self.client.put(path, json.dumps(data), 'application/json')
        return None

    def assign_user_to_code(self, coupon_id, emails, codes):
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_assignment_email.delay'):
            self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/assign/'.format(coupon_id),
                {
                    'template': 'Test template',
                    'template_greeting': TEMPLATE_GREETING,
                    'template_closing': TEMPLATE_CLOSING,
                    'emails': emails,
                    'codes': codes
                }
            )

    def revoke_code_from_user(self, coupon_id, email, code):
        with mock.patch('ecommerce.extensions.offer.utils.send_offer_update_email.delay'):
            self.get_response(
                'POST',
                '/api/v2/enterprise/coupons/{}/revoke/'.format(coupon_id),
                {'assignments': [{'email': email, 'code': code}]}
            )

    def test_view_returns_appropriate_data(self):
        """
        View should return the appropriate data for given user_email.
        """
        oa_code1 = OfferAssignment.objects.get(
            user_email=self.user.email,
            offer__vouchers__coupon_vouchers__coupon__id=self.coupon1.id
        ).code
        oa_code2 = OfferAssignment.objects.get(
            user_email=self.user.email,
            offer__vouchers__coupon_vouchers__coupon__id=self.coupon2.id
        ).code
        oa_code3 = OfferAssignment.objects.filter(
            user_email=self.user.email,
            offer__vouchers__coupon_vouchers__coupon__id=self.coupon3.id
        ).last().code

        response = self.client.get(OFFER_ASSIGNMENT_SUMMARY_LINK).json()

        assert response['count'] == 3
        # To get the code to verify our response, filter using the coupon
        # id these offerAssignments were created for
        for result in response['results']:
            if result['code'] == oa_code1:
                assert result['benefit_value'] == 40
                assert result['usage_type'] == 'Percentage'
                assert result['redemptions_remaining'] == 1
                assert result['catalog'] == 'aaaaaaaa-2c44-487b-9b6a-24eee973f9a4'
            elif result['code'] == oa_code2:
                assert result['benefit_value'] == 13.37
                assert result['usage_type'] == 'Absolute'
                assert result['redemptions_remaining'] == 1
                assert result['catalog'] == 'bbbbbbbb-2c44-487b-9b6a-24eee973f9a4'
            elif result['code'] == oa_code3:
                assert result['benefit_value'] == 444
                assert result['usage_type'] == 'Absolute'
                assert result['redemptions_remaining'] == 7
                assert result['catalog'] == 'cccccccc-2c44-487b-9b6a-24eee973f9a4'
            else:  # To test if response has something in it it shouldn't
                assert False


@ddt.ddt
class OfferAssignmentEmailTemplatesViewSetTests(JwtMixin, TestCase):
    """
    Test the enterprise offer assignment templates functionality with role based access control.
    """
    def setUp(self):
        super(OfferAssignmentEmailTemplatesViewSetTests, self).setUp()
        self.user = self.create_user(is_staff=True, email='test@example.com')
        self.client.login(username=self.user.username, password=self.password)
        self.enterprise = '5c0dd495-e726-46fa-a6a8-2d8d26c716c9'
        self.url = reverse(
            'api:v2:enterprise-offer-assignment-email-template-list',
            kwargs={'enterprise_customer': self.enterprise}
        )

        self.enterprise_customer = {'name': 'test enterprise', 'id': self.enterprise}

        # Prepare permissions for hitting the endpoint
        self.role = EcommerceFeatureRole.objects.get(name=ENTERPRISE_COUPON_ADMIN_ROLE)
        EcommerceFeatureRoleAssignment.objects.get_or_create(
            role=self.role,
            user=self.user,
            enterprise_id=self.enterprise_customer['id']
        )
        self.set_jwt_cookie(
            system_wide_role=SYSTEM_ENTERPRISE_ADMIN_ROLE, context=self.enterprise_customer['id']
        )

    def create_template_data(self, email_type, greeting=None, closing=None, status_code=status.HTTP_201_CREATED):

        data = {'email_type': email_type}
        if greeting:
            data['email_greeting'] = greeting
        if closing:
            data['email_closing'] = closing

        response = self.client.post(self.url, json.dumps(data), 'application/json')
        assert response.status_code == status_code

        return response.json()

    def create_multiple_templates_data(self):
        types = ['assign', 'assign', 'remind', 'remind', 'revoke', 'revoke']
        greetings = ['GREETING 1', 'GREETING 2', 'GREETING 3', 'GREETING 4', 'GREETING 5', 'GREETING 6']
        closings = ['CLOSING 1', 'CLOSING 2', 'CLOSING 3', 'CLOSING 4', 'CLOSING 5', 'CLOSING 6']

        # create multiple templates of each email type for an enterprise
        for email_type, email_greeting, email_closing in zip(types, greetings, closings):
            self.create_template_data(email_type, email_greeting, email_closing)

    def verify_template_data(self, template, email_type, email_greeting, email_closing, active):
        assert template['enterprise_customer'] == self.enterprise
        assert template['email_type'] == email_type
        assert template['email_body'] == settings.OFFER_ASSIGNMEN_EMAIL_TEMPLATE_BODY_MAP[email_type]
        assert template['email_greeting'] == email_greeting
        assert template['email_closing'] == email_closing
        assert template['active'] == active

    def test_return_all_templates_for_enterprise(self):
        """
        Verify that view returns all(assign, remind, revoke) templates for an enterprise.
        """
        expected_template_data = [
            {
                'email_type': 'assign',
                'email_greeting': 'GREETING 2',
                'email_closing': 'CLOSING 2',
                'active': True
            },
            {
                'email_type': 'remind',
                'email_greeting': 'GREETING 4',
                'email_closing': 'CLOSING 4',
                'active': True
            },
            {
                'email_type': 'revoke',
                'email_greeting': 'GREETING 6',
                'email_closing': 'CLOSING 6',
                'active': True
            },
            {
                'email_type': 'assign',
                'email_greeting': 'GREETING 1',
                'email_closing': 'CLOSING 1',
                'active': False
            },
            {
                'email_type': 'remind',
                'email_greeting': 'GREETING 3',
                'email_closing': 'CLOSING 3',
                'active': False
            },
            {
                'email_type': 'revoke',
                'email_greeting': 'GREETING 5',
                'email_closing': 'CLOSING 5',
                'active': False
            },
        ]

        self.create_multiple_templates_data()

        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK

        recieved_template_data = response.json()['results']
        assert len(recieved_template_data) == 6

        # verify received templates data
        for recieved_template, expected_template in zip(recieved_template_data, expected_template_data):
            self.verify_template_data(
                recieved_template,
                expected_template['email_type'],
                expected_template['email_greeting'],
                expected_template['email_closing'],
                expected_template['active'],
            )

    @ddt.data(
        [
            {
                'email_type': 'assign',
                'email_greeting': 'GREETING 2',
                'email_closing': 'CLOSING 2',
                'active': True
            },
            {
                'email_type': 'assign',
                'email_greeting': 'GREETING 1',
                'email_closing': 'CLOSING 1',
                'active': False
            },
        ],
        [
            {
                'email_type': 'remind',
                'email_greeting': 'GREETING 4',
                'email_closing': 'CLOSING 4',
                'active': True
            },
            {
                'email_type': 'remind',
                'email_greeting': 'GREETING 3',
                'email_closing': 'CLOSING 3',
                'active': False
            },
        ],
        [
            {
                'email_type': 'revoke',
                'email_greeting': 'GREETING 6',
                'email_closing': 'CLOSING 6',
                'active': True
            },
            {
                'email_type': 'revoke',
                'email_greeting': 'GREETING 5',
                'email_closing': 'CLOSING 5',
                'active': False
            },
        ],
    )
    def test_return_specific_templates_for_enterprise(self, expected_template_data):
        """
        Verify that view only returns specific type of templates for an enterprise if for a specific email_type.
        """
        self.create_multiple_templates_data()

        email_type = expected_template_data[0]['email_type']
        response = self.client.get('{}?email_type={}'.format(self.url, email_type))
        assert response.status_code == status.HTTP_200_OK

        recieved_template_data = response.json()['results']
        assert len(recieved_template_data) == 2
        for recieved_template, expected_template in zip(recieved_template_data, expected_template_data):
            self.verify_template_data(
                recieved_template,
                expected_template['email_type'],
                expected_template['email_greeting'],
                expected_template['email_closing'],
                expected_template['active'],
            )

    @ddt.data(
        {
            'email_type': 'assign',
            'expected_email_greeting': 'GREETING 2',
            'expected_email_closing': 'CLOSING 2',
        },
        {
            'email_type': 'remind',
            'expected_email_greeting': 'GREETING 4',
            'expected_email_closing': 'CLOSING 4',
        },
        {
            'email_type': 'revoke',
            'expected_email_greeting': 'GREETING 6',
            'expected_email_closing': 'CLOSING 6',
        },
    )
    @ddt.unpack
    def test_return_active_template_for_enterprise(self, email_type, expected_email_greeting, expected_email_closing):
        """
        Verify that view returns only a single active template for an enterprise for a specific email type.
        """
        self.create_multiple_templates_data()

        response = self.client.get('{}?email_type={}&active=1'.format(self.url, email_type))
        assert response.status_code == status.HTTP_200_OK

        templates = response.json()['results']
        assert len(templates) == 1
        self.verify_template_data(templates[0], email_type, expected_email_greeting, expected_email_closing, True)

    def test_retrieve_template_for_enterprise(self):
        """
        Verify that view's retreive action work as expected.
        """
        email_type = 'assign'
        email_greeting = 'greeting'
        email_closing = 'closing'

        created_template = self.create_template_data(email_type, email_greeting, email_closing)

        response = self.client.get('{}{}/'.format(self.url, created_template['id']))
        assert response.status_code == status.HTTP_200_OK

        received_template = response.json()
        self.verify_template_data(received_template, email_type, email_greeting, email_closing, True)

    @ddt.data('assign', 'remind', 'revoke')
    def test_post(self, email_type):
        """
        Verify that view correctly performs HTTP POST.
        """
        templates = []

        # make multiple POSTs to verify that active field for old templates of a specific email type is set to False
        for __ in range(2):
            email_greeting = 'GREETING {}'.format(uuid4().hex.upper()[0:6])
            email_closing = 'CLOSING {}'.format(uuid4().hex.upper()[0:6])

            template = self.create_template_data(email_type, email_greeting, email_closing)
            self.verify_template_data(template, email_type, email_greeting, email_closing, True)

            templates.append(template)

        # verify that active is set to False for old template
        assert OfferAssignmentEmailTemplates.objects.get(id=templates[0]['id']).active is False

    @ddt.data('assign', 'remind', 'revoke')
    def test_post_with_unsafe_data(self, email_type):
        """
        Verify that view correctly performs HTTP POST on unsafe data.
        """
        email_greeting = '<script>document.getElementById("greeting").innerHTML = "GREETING!";</script>'
        email_closing = '<script>document.getElementById("closing").innerHTML = "CLOSING!";</script>'

        template = self.create_template_data(email_type, email_greeting, email_closing)
        assert template['email_greeting'] == bleach.clean(email_greeting)
        assert template['email_closing'] == bleach.clean(email_closing)

    @ddt.data('assign', 'remind', 'revoke')
    def test_post_with_empty_template_values(self, email_type):
        """
        Verify that view correctly performs HTTP POST with empty template values.
        """
        email_greeting = ''
        email_closing = ''

        template = self.create_template_data(email_type, email_greeting, email_closing)
        self.verify_template_data(template, email_type, email_greeting, email_closing, True)

    @ddt.data('assign', 'remind', 'revoke')
    def test_post_with_optional_fields(self, email_type):
        """
        Verify that view correctly performs HTTP POST with optional fields.
        """
        template = self.create_template_data(email_type, None, None)
        self.verify_template_data(template, email_type, '', '', True)

    @ddt.data('assign', 'remind', 'revoke')
    def test_post_with_max_length_field_validation(self, email_type):
        """
        Verify that view HTTP POST return error email closing/greeting exceeds field max length.
        """
        email_greeting = "HI" * 160
        email_closing = "HI" * 160

        response = self.create_template_data(email_type, email_greeting, email_closing, status.HTTP_400_BAD_REQUEST)
        assert response == {
            "email_greeting": [
                "Ensure this field has no more than 300 characters."
            ],
            "email_closing": [
                "Ensure this field has no more than 300 characters."
            ]
        }
