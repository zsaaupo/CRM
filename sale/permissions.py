CHALAN_VIEW = ('sale.view_chalan',)
CHALAN_ITEM_VIEW = ('sale.view_chalanitem',)
CHALAN_ADD = ('sale.add_chalan', 'sale.add_chalanitem')
CHALAN_CREATE = (*CHALAN_VIEW, *CHALAN_ADD)
CHALAN_EDIT = (*CHALAN_VIEW, *CHALAN_ITEM_VIEW, 'sale.change_chalan',
               'sale.add_chalanitem', 'sale.change_chalanitem', 'sale.delete_chalanitem')
CHALAN_DELETE = ('sale.delete_chalan', 'sale.delete_chalanitem')

PACKING_SLIP_VIEW = (*CHALAN_VIEW, 'sale.view_packingslip')
PACKING_SLIP_PRINT = (*PACKING_SLIP_VIEW, *CHALAN_ITEM_VIEW)
PACKING_SLIP_GENERATE = (*PACKING_SLIP_PRINT, 'sale.add_packingslip')

COURIER_VIEW = ('sale.view_courier',)
COURIER_CREATE = (*COURIER_VIEW, 'sale.add_courier')
COURIER_EDIT = (*COURIER_VIEW, 'sale.change_courier')

DELIVERY_VIEW = (*CHALAN_VIEW, 'sale.view_delivery')
DELIVERY_ASSIGN = (*DELIVERY_VIEW, 'sale.add_delivery')
DELIVERY_CHANGE = (*DELIVERY_VIEW, 'sale.change_delivery')
