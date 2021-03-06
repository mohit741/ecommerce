# -*- coding: utf-8 -*-
# Generated by Django 1.11.27 on 2020-01-16 06:29
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payment', '0025_auto_20200114_0530'),
    ]

    operations = [
        migrations.AddField(
            model_name='razorpaytransaction',
            name='order_id',
            field=models.CharField(blank=True, db_index=True, max_length=32, null=True),
        ),
        migrations.AlterField(
            model_name='source',
            name='card_type',
            field=models.CharField(blank=True, choices=[('visa', 'Visa'), ('discover', 'Discover'), ('mastercard', 'MasterCard'), ('american_express', 'American Express')], max_length=255, null=True),
        ),
    ]
