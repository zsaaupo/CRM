from django import forms

from .models import Brand, Product, ProductCategory, ProductType, Status, UnitOfMeasure


class CatalogForm(forms.ModelForm):
    """Render the API's writable fields using the existing workspace styles."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            checkbox = isinstance(field.widget, forms.CheckboxInput)
            field.widget.attrs.update({
                'class': 'inventory-check-input' if checkbox else 'form-input',
                'aria-describedby': f'id_{name}_hint id_{name}_error',
            })
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs['rows'] = 3
            if isinstance(field, forms.JSONField):
                field.widget.attrs['data-json-field'] = 'true'
            if isinstance(field.widget, forms.ClearableFileInput):
                field.widget.attrs['accept'] = 'image/*'
            if isinstance(field, forms.DecimalField):
                field.widget.attrs['min'] = '0'
            if isinstance(field, forms.ModelChoiceField) and not field.required:
                field.widget.attrs['data-nullable-field'] = 'true'


class ProductForm(CatalogForm):
    class Meta:
        model = Product
        fields = [
            'sku', 'name', 'barcode', 'product_type', 'status', 'category', 'brand',
            'unit', 'variant_of', 'description', 'image', 'manufacturer',
            'manufacturer_part_number', 'attributes', 'currency', 'cost_price',
            'sale_price', 'minimum_sale_price', 'Whole_sale_price', 'product_tax', 'is_returnable',
        ]
        labels = {'sku': 'SKU', 'Whole_sale_price': 'Wholesale price', 'product_tax': 'Tax rate (%)',
                  'variant_of': 'Base product', 'attributes': 'Specifications (JSON)', 'is_returnable': 'Returnable'}
        help_texts = {'attributes': 'Optional specifications, e.g. {"color": "blue", "size": "M"}.',
                      'variant_of': 'Select a base product only when adding a variant.',
                      'currency': 'Three uppercase letters, such as BDT or USD.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['variant_of'].queryset = Product.objects.filter(variant_of__isnull=True).order_by('name', 'pk')
        self.fields['currency'].widget.attrs.update({'placeholder': 'BDT', 'pattern': '[A-Z]{3}'})
        self.fields['product_tax'].widget.attrs['max'] = '100'


class ProductCategoryForm(CatalogForm):
    class Meta:
        model = ProductCategory
        fields = ['name', 'code', 'description', 'is_active']
        labels = {'is_active': 'Active'}


class BrandForm(CatalogForm):
    class Meta:
        model = Brand
        fields = ['name', 'website', 'description', 'is_active']
        labels = {'is_active': 'Active'}


class UnitOfMeasureForm(CatalogForm):
    class Meta:
        model = UnitOfMeasure
        fields = ['name', 'code', 'is_active']
        labels = {'is_active': 'Active'}


class ProductTypeForm(CatalogForm):
    class Meta:
        model = ProductType
        fields = ['name', 'code', 'description', 'is_active']
        labels = {'is_active': 'Active'}
        help_texts = {'code': 'Choose a unique code, such as rental. The code cannot change after creation.'}


class StatusForm(CatalogForm):
    class Meta:
        model = Status
        fields = ['name', 'code', 'description', 'is_active']
        labels = {'is_active': 'Active'}
        help_texts = {'code': 'Choose a unique code, such as awaiting-review. The code cannot change after creation.'}
