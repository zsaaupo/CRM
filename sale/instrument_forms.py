from decimal import Decimal
from datetime import date
from uuid import uuid4

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from people.models import Party
from .models import Coupon, GiftCard


class InstrumentForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not field.widget.is_hidden and not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-input'


class AmountForm(InstrumentForm):
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    request_key = forms.UUIDField(required=False, initial=uuid4, widget=forms.HiddenInput)


class ExpiryForm(InstrumentForm):
    expired_date = forms.DateField(label='Expiration date', widget=forms.DateInput(attrs={'type': 'date'}))

    def clean_expired_date(self):
        value = self.cleaned_data['expired_date']
        if value and value < timezone.localdate():
            raise forms.ValidationError('Choose today or a later date.')
        return value


class GiftCardForm(AmountForm, ExpiryForm):
    is_deposit = forms.BooleanField(required=False, label='Customer deposit account (no expiry)')
    expired_date = forms.DateField(required=False, label='Expiration date', widget=forms.DateInput(attrs={'type': 'date'}))
    card_no = forms.RegexField(r'^[0-9]{16}\Z', required=False, label='Card number',
                               widget=forms.TextInput(attrs={'inputmode': 'numeric', 'maxlength': 16}))
    customer = forms.ModelChoiceField(queryset=Party.objects.filter(is_customer=True, is_active=True), required=False)
    user = forms.ModelChoiceField(queryset=get_user_model().objects.filter(is_active=True), required=False,
                                  label='Staff owner')

    def clean(self):
        data = super().clean()
        if data.get('customer') and data.get('user'):
            raise forms.ValidationError('Choose one owner, or leave both blank for a bearer card.')
        if data.get('is_deposit'):
            if not data.get('customer'):
                self.add_error('customer', 'Select the owner of the deposit.')
            elif GiftCard.objects.filter(customer=data['customer'], is_deposit=True).exclude(
                    entries__request_key=data.get('request_key')).exists():
                self.add_error('customer', 'This customer already has a deposit account. Recharge that account.')
            data['expired_date'] = date.max
        elif not data.get('expired_date'):
            self.add_error('expired_date', 'Choose an expiration date.')
        return data


class CouponForm(InstrumentForm):
    code = forms.RegexField(r'^[A-Za-z0-9_-]+\Z', max_length=40, required=False)
    kind = forms.ChoiceField(label='Discount type', choices=Coupon.Kind.choices)
    value = forms.DecimalField(label='Discount value', max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    quantity = forms.IntegerField(label='Total uses across all tills', min_value=1, max_value=2147483647)

    def clean_code(self):
        return self.cleaned_data['code'].upper()

    def clean(self):
        data = super().clean()
        if data.get('kind') == Coupon.Kind.PERCENT and data.get('value', 0) > 100:
            self.add_error('value', 'Percentage cannot exceed 100.')
        return data

