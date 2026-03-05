{
    'name': "LAYAMED CONNECTOR",
    'description': "LAYAMED - CHICCORNER",
    'summary': "",
    'author': 'LAYAMED CONSULTING',
    'category': 'base',
    'version': '1.0',
    'description': """
        This module introduces custom features for LAYAMED
    """,
    'author': 'LAYAMED CONSULTING',
    'website': 'http://www.layamedconsulting.com',
    'category': '',
    'depends': ['base','product','queue_job','sale_management', 'stock', 'account'],
    'data': [
        'views/promotion.xml',
        'security/ir.model.access.csv',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
