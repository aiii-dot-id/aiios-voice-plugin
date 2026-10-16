"""Browser-reported capture settings are diagnostics, not acoustic evidence."""


def capture_processing(value):
    if value is None:
        return None
    allowed = {
        "echo_cancellation", "noise_suppression", "auto_gain_control",
        "sample_rate", "tested",
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("capture processing requires declared diagnostic fields")
    result = dict(value)
    for key in ("echo_cancellation", "noise_suppression", "auto_gain_control"):
        if key in result and result[key] is not None and type(result[key]) is not bool:
            raise ValueError(f"{key} must be boolean or unknown")
    if "sample_rate" in result and result["sample_rate"] is not None:
        rate = result["sample_rate"]
        if type(rate) is not int or not 8000 <= rate <= 384000:
            raise ValueError("capture sample_rate must be a plausible whole rate")
    if "tested" in result and result["tested"] is not False:
        raise ValueError("browser capture settings cannot assert acoustic qualification")
    return result
