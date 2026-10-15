"""Inventory report: a small program with branches, loops, errors and classes."""
import math
from pathlib import Path

TAX_RATE = 0.08
LOW_STOCK = 5


class Item:
    def __init__(self, name, price, quantity):
        self.name = name
        self.price = price
        self.quantity = quantity

    def total(self):
        return self.price * self.quantity

    def is_low(self):
        return self.quantity < LOW_STOCK


def area(radius: float) -> float:
    if radius < 0:
        raise ValueError("radius must be positive")
    return math.pi * radius ** 2


def parse_line(line):
    parts = line.split(",")
    if len(parts) != 3:
        return None
    name, price, quantity = parts
    return Item(name.strip(), float(price), int(quantity))


def load_items(lines):
    items = []
    for line in lines:
        if not line.strip():
            continue
        item = parse_line(line)
        if item is None:
            print("skipping bad line:", line)
            continue
        items.append(item)
    return items


def classify(item):
    match item.quantity:
        case 0:
            return "out of stock"
        case q if q < LOW_STOCK:
            return "low"
        case _:
            return "ok"


def report(items):
    total = 0
    for item in items:
        subtotal = item.total()
        if item.is_low():
            print(item.name, "is low")
        elif subtotal > 100:
            print(item.name, "is a big line")
        else:
            print(item.name, classify(item))
        total += subtotal
    else:
        print("all items seen")
    return total * (1 + TAX_RATE)


def main():
    lines = ["bolt, 0.5, 100", "", "wheel, 30, 3", "broken line"]
    items = load_items(lines)
    attempts = 0
    grand_total = 0
    while attempts < 3:
        attempts += 1
        try:
            grand_total = report(items)
        except ValueError as err:
            print(err)
            continue
        else:
            break
        finally:
            print("attempt", attempts)
    with open(Path("out.txt"), "w") as fh:
        fh.write(f"{grand_total:.2f}\n")
    print("circle for fun:", area(2))


if __name__ == "__main__":
    main()
