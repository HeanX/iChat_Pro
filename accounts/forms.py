import re

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import UserProfile


class RegistrationForm(UserCreationForm):
    """Custom registration form with enhanced validation."""

    email = forms.EmailField(
        required=True,
        error_messages={
            'required': 'Email address is required.',
            'invalid': 'Please enter a valid email address.',
        },
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()

        if len(username) < 3:
            raise forms.ValidationError(
                'Username must be at least 3 characters long.',
            )

        if not re.match(r'^[\w.@+-]+$', username):
            raise forms.ValidationError(
                'Username may only contain letters, digits, '
                'and @/./+/-/_ characters.',
            )

        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError(
                'A user with that username already exists.',
            )

        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                'A user with that email address already exists.',
            )

        return email

    def clean_password1(self):
        password = self.cleaned_data.get('password1', '')

        if len(password) < 8:
            raise forms.ValidationError(
                'Password must be at least 8 characters long.',
            )

        # Require at least one letter and one digit
        if not re.search(r'[A-Za-z]', password):
            raise forms.ValidationError(
                'Password must contain at least one letter.',
            )

        if not re.search(r'\d', password):
            raise forms.ValidationError(
                'Password must contain at least one digit.',
            )

        # Check against username
        username = self.cleaned_data.get('username', '')
        if username and username.lower() in password.lower():
            raise forms.ValidationError(
                'Password cannot be too similar to your username.',
            )

        return password


class ProfileForm(forms.ModelForm):
    """Edit nickname, bio, avatar, and username/name fields."""

    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    username = forms.CharField(max_length=150, required=True)

    class Meta:
        model = UserProfile
        fields = ('nickname', 'bio', 'avatar')

    def __init__(self, *args, **kwargs):
        self._user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip().lower()

        if len(username) < 5:
            raise forms.ValidationError(
                'Username must be at least 5 characters long.',
            )

        if not re.match(r'^[a-z0-9_]+$', username):
            raise forms.ValidationError(
                'Username may only contain lowercase letters, digits, and underscores.',
            )

        # Check uniqueness excluding the current user
        qs = User.objects.filter(username__iexact=username)
        if self._user:
            qs = qs.exclude(pk=self._user.pk)
        if qs.exists():
            raise forms.ValidationError(
                'A user with that username already exists.',
            )

        return username
