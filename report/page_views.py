from django.urls import reverse

from account.page_views import ProtectedAccountPageView
from report.analytics import REPORT_REQUIRED_PERMS


class AnalyticsReportPageView(ProtectedAccountPageView):
    template_name = 'report/analytics.html'

    def get_permission_required(self):
        slug = self.kwargs['slug']
        return REPORT_REQUIRED_PERMS.get(slug, ())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        slug = self.kwargs['slug']
        ctx.update({
            'page_section': 'report',
            'page_label': f"{slug.replace('-', ' ').title()} Report",
            'api_url': reverse('report:analytics-api', args=[slug]),
        })
        return ctx
