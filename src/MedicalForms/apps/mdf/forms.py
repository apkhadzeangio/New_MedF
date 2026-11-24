from django import forms

from .models import NivForm


class LoginForm(forms.Form):
    username = forms.CharField(required=True, max_length=150)
    password = forms.CharField(required=True, max_length=128)
    next = forms.CharField(required=False)


class NivFormForm(forms.ModelForm):

    class Meta:
        model = NivForm
        fields = '__all__'


class PhotoForm(forms.Form):
    full_name = forms.CharField(label='სახელი და გვარი (ბლანკებისთვის)', max_length=150, required=False)
    photo = forms.ImageField(label='ფოტო', required=False)

    def clean_photo(self):
        photo = self.cleaned_data.get('photo')
        if photo and photo.size > 10 * 1024 * 1024:
            raise forms.ValidationError('ფოტო ძალიან დიდია (მაქს. 10 MB).')
        return photo
