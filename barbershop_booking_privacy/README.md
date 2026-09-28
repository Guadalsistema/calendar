# Barbershop booking privacy

This module narrows internal barber access to booking records whose selected
combination contains a user resource owned by that barber. Booking-related
calendar events are similarly restricted while unrelated calendar events
remain subject to Odoo's normal calendar rules. A partner must be explicitly
marked as a private booking customer before the customer-specific fence applies;
ordinary partners are not narrowed by this module.

Managers inherit the resource-booking manager role and retain full booking and
calendar-event CRUD. Portal and public users remain subject to the upstream
resource-booking token and partner rules. Chatter messages, followers and
attachments are checked against parent-record visibility; attendees remain
protected through the booking-event rule. Integrations that bypass ORM access
checks or introduce alternate data paths still require an independent audit.

## Generic shop-choice API

The abstract model `barbershop.booking.choice` is a transport-independent API
for service choices. `eligible_combinations(company_id, type_id, mode=None,
location_id=None)` returns the active eligible combination recordset. `mode`
is `location` (requiring an active location resource ID) or `independent`;
omitting it is accepted only when the service exposes no company location
choices. Website publication is intentionally not considered.

`available_slots(company_id, type_id, mode=None, location_id=None, start_dt=None,
end_dt=None, combination_id=None)` returns `{combination_id: {date: [aware
datetimes]}}`; this preserves which candidates offer each slot. `confirm_booking`
has parameters `(company_id, type_id, mode=None, location_id=None,
combination_id=None, when=None, contact=None)`. A false combination selects
the first currently free candidate deterministically; otherwise the supplied
combination must be eligible. `when` must be timezone-aware. `contact` requires
`name`, `email`, and `phone`. Call confirmation within the caller's transaction
or savepoint; the method locks resources, revalidates, then creates the private
customer and confirmed booking atomically.
