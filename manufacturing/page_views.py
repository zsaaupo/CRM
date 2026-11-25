from django.urls import reverse
from account.page_views import ProtectedAccountPageView


class ManufacturingHomePageView(ProtectedAccountPageView):
    template_name = 'manufacturing/overview.html'
    permission_required = ('manufacturing.view_productionorder',)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            'page_section': 'manufacturing',
            'page_label': 'Manufacturing & In-House Production',
            'raw_materials_url': reverse('manufacturing:raw-material-list'),
            'boms_url': reverse('manufacturing:bom-list'),
            'production_url': reverse('manufacturing:production-order-list'),
        })
        return ctx
