from datetime import datetime


# ---------- Validation limits ----------
MIN_MEALS = 1
MAX_MEALS = 10000
MAX_NAME_LENGTH = 100
MAX_ADDRESS_LENGTH = 300


def validate_donation(
    donor_name: str,
    food_type: str,
    meals: int,
    address: str,
    deadline: datetime,
) -> list[tuple[str, dict]]:
    """Return translation keys and parameters for validation errors."""
    errors = []

    donor_name = donor_name.strip()
    food_type = food_type.strip()
    address = address.strip()

    # Donor name
    if not donor_name:
        errors.append(("donation.error.name_required", {}))
    elif len(donor_name) > MAX_NAME_LENGTH:
        errors.append((
            "donation.error.name_too_long",
            {"maximum": MAX_NAME_LENGTH},
        ))

    # Food type
    if not food_type:
        errors.append(("donation.error.food_type_required", {}))
    elif len(food_type) > MAX_NAME_LENGTH:
        errors.append((
            "donation.error.food_type_too_long",
            {"maximum": MAX_NAME_LENGTH},
        ))

    # Number of meals
    if isinstance(meals, bool) or not isinstance(meals, int):
        errors.append(("donation.error.meals_integer", {}))
    elif meals < MIN_MEALS:
        errors.append((
            "donation.error.meals_min",
            {"minimum": MIN_MEALS},
        ))
    elif meals > MAX_MEALS:
        errors.append((
            "donation.error.meals_max",
            {"maximum": MAX_MEALS},
        ))

    # Pickup address
    if not address:
        errors.append(("donation.error.address_required", {}))
    elif len(address) > MAX_ADDRESS_LENGTH:
        errors.append((
            "donation.error.address_too_long",
            {"maximum": MAX_ADDRESS_LENGTH},
        ))

    # Pickup deadline uses local device time.
    if deadline <= datetime.now():
        errors.append(("donation.error.deadline_future", {}))

    return errors