import factory
from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role

PASSWORD = "correct-horse-battery-staple"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"person{n}@example.edu")
    full_name = factory.Sequence(lambda n: f"Person {n}")
    password = factory.django.Password(PASSWORD)

    @factory.post_generation
    def verified(self, create: bool, extracted: bool | None, **kwargs: object) -> None:
        if create and extracted is not False:
            EmailAddress.objects.create(user=self, email=self.email, verified=True, primary=True)


class CenterFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Center

    name = factory.Sequence(lambda n: f"Writing Center {n}")
    slug = factory.Sequence(lambda n: f"center-{n}")


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Membership

    user = factory.SubFactory(UserFactory)
    center = factory.SubFactory(CenterFactory)
    role = Role.STUDENT


def give_mfa(user: User) -> None:
    """Record an authenticator app for a staff user so the 2FA gate lets them through."""
    Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={"secret": "x"})
