from django.urls import reverse

from account.page_views import ProtectedAccountPageView
from report.analytics import REPORT_CATALOG, REPORT_REQUIRED_PERMS, _check_report_permission


class AnalyticsReportPageView(ProtectedAccountPageView):
    template_name = 'report/analytics.html'

    def has_permission(self):
        slug = self.kwargs['slug']
        try:
            _check_report_permission(self.request.user, slug)
            return True
        except Exception:
            return False

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        slug = self.kwargs['slug']
        ctx.update({
            'page_section': 'report',
            'report_slug': slug,
            'report_catalog': REPORT_CATALOG,
            'page_label': f"{slug.replace('-', ' ').title()} Report",
            'api_url': reverse('report:analytics-api', args=[slug]),
        })
        return ctx
