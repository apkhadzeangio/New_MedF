from django import forms

from .models import NivForm


class LoginForm(forms.Form):
    username = forms.CharField(required=True, max_length=150)
    password = forms.CharField(required=True, max_length=128)
    remember_me = forms.BooleanField(required=False)
    next = forms.CharField(required=False)


class NivFormFrom(forms.ModelForm):

    class Meta:
        model = NivForm
        fields = '__all__'
