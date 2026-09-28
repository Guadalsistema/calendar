# Resource booking locations

This independent Odoo 18 addon extends OCA `resource_booking`; it does not patch that addon. Install it alongside OCA Resource Booking, then create an `Other Address` partner for each shop/site and a resource of type **Location** linked to that address. Set the resource's working calendar and timezone. Latitude/longitude are editable from the resource but stored on the linked partner; the resource's image is its own image field.

## Scheduling behavior

A combination may have no location or exactly one location. It may be location-only; there are no assumptions about barber, chair, or other resource counts. A location is a named/addressed place, not exclusive capacity: appointment-derived busy intervals do not block other appointments from using that location. Its calendar, working hours, resource leaves, and all other non-booking scheduling constraints continue to apply. Other resources in the combination remain exclusive and continue to block conflicts. Locationless combinations use OCA Resource Booking behavior unchanged.

Each combination also has an optional **Availability calendar**. Use it to
restrict a worker-and-shop pair to recurring days or hours. This calendar is
intersected with the booking type, shop, worker, and other resource calendars;
it does not replace them like OCA's **Forced calendar** does. For example, the
same worker can have one combination available on Mondays at Shop 1 and another
available on Tuesdays at Shop 2.

Only active location resources must be unique per address partner and company. Inactive duplicates are allowed. Location resources require an Other Address partner, a compatible company, a working calendar, a valid timezone, and in-range coordinates when supplied (zero is valid). A second location in one combination is rejected regardless of whether it is added through combination membership or resource type changes. Changes that would silently alter scheduled/confirmed booking location or combination membership are rejected.

## Consumer API

For a combination record, `_get_location_resource()` returns its location resource (empty when none), and `_get_location_address()` returns the formatted partner address or an empty string. Resource bookings derive their meeting location from that address when present, otherwise they retain the OCA booking-type/meeting location behavior.

The `tests` addon package contains TransactionCase coverage for validation, resource and combination invariants, location-only/address behavior, shared-location and exclusive-resource scheduling, and the locationless busy-interval path.
