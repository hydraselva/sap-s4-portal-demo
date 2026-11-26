"""GST split and Indian-format amount helpers (pure functions, fully unit-tested)."""
from decimal import ROUND_HALF_UP, Decimal


def money(x):
    return Decimal(str(x or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def split_gst(from_state, to_state, taxable, rate=18.0):
    """
    Intra-state supply → CGST + SGST (half each); inter-state → IGST.
    Unknown state on either side is treated as inter-state (the safer default).
    """
    taxable = money(taxable)
    rate = Decimal(str(rate))
    if from_state and to_state and from_state == to_state:
        half = money(taxable * rate / 200)
        return {"cgst": half, "sgst": half, "igst": money(0)}
    return {"cgst": money(0), "sgst": money(0), "igst": money(taxable * rate / 100)}


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_1000(n):
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")
    return _ONES[n // 100] + " Hundred" + (" " + _below_1000(n % 100) if n % 100 else "")


def amount_in_words(amount):
    """1234567.5 → 'Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven and Fifty Paise Only'"""
    amount = abs(money(amount))
    rupees, paise = int(amount), int((amount - int(amount)) * 100)
    if rupees == 0:
        words = "Zero"
    else:
        parts, n = [], rupees
        crore, n = divmod(n, 10_000_000)
        lakh, n = divmod(n, 100_000)
        thousand, n = divmod(n, 1_000)
        if crore:
            parts.append(amount_in_words_int(crore) + " Crore")
        if lakh:
            parts.append(_below_1000(lakh) + " Lakh")
        if thousand:
            parts.append(_below_1000(thousand) + " Thousand")
        if n:
            parts.append(_below_1000(n))
        words = " ".join(parts)
    out = f"Rupees {words}"
    if paise:
        out += f" and {_below_1000(paise)} Paise"
    return out + " Only"


def amount_in_words_int(n):
    """Whole-number words, used for the crore part (which can exceed 999)."""
    return amount_in_words(n).removeprefix("Rupees ").removesuffix(" Only")


def inr(x):
    """Indian digit grouping: 1234567.8 → '12,34,567.80'"""
    x = money(x)
    sign = "-" if x < 0 else ""
    whole, frac = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{whole}.{frac}"
