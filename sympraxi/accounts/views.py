from allauth.account.views import LoginView


class LocalLoginView(LoginView):
    """Break-glass password sign-in for site administrators.

    With SYMPRAXI_SSO_ONLY on, the regular sign-in page shows only the SSO buttons.
    This page keeps the password form for the day the identity provider is down or
    misconfigured. It grants nothing extra: `AccountAdapter.pre_login()` refuses
    every non-SSO sign-in except a site administrator's, whichever page it came
    from, and two-factor login still applies.
    """

    extra_context = {"break_glass": True}
