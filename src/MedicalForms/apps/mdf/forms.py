from django import forms

from .models import NivForm


class LoginForm(forms.Form):
    username = forms.CharField(required=True)
    password = forms.CharField(required=True)
    next = forms.CharField(required=False)

    class Meta:
        fields = ('username', 'password', 'next')


class NivFormFrom(forms.ModelForm):

    class Meta:
        model = NivForm
        fields = '__all__'
