from __future__ import absolute_import

from django.apps import AppConfig


class CheckoutAppConfig(AppConfig):
    name = 'ecommerce.extensions.checkout'
    verbose_name = 'Checkout'

    def ready(self):
        super(CheckoutAppConfig, self).ready()

        # noinspection PyUnresolvedReferences
        import ecommerce.extensions.checkout.signals  # pylint: disable=unused-import, import-outside-toplevel
