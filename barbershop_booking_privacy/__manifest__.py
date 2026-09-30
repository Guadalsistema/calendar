{
    "name": "Barbershop Booking Privacy",
    "version": "18.0.1.0.0",
    "license": "AGPL-3",
    "depends": ["resource_booking_location", "calendar"],
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "security/ir_rule.xml",
        "views/portal_templates.xml",
    ],
    "external_dependencies": {"python": ["pytz"]},
    "installable": True,
}
