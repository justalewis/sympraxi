from django.urls import path

from sympraxi.forms import survey_views

app_name = "survey"

# Mounted at /survey/. Opened from an emailed link without signing in: the token
# is what opens the page. tests/test_url_inventory.py classifies these.
urlpatterns = [
    path("thanks/", survey_views.thanks, name="thanks"),
    path("<str:token>/", survey_views.answer, name="answer"),
]
