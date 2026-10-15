// Inventory report: a small program with branches, loops, errors and classes.
import { readFileSync } from "fs";

const TAX_RATE = 0.08;
const LOW_STOCK = 5;

class Item {
  constructor(name, price, quantity) {
    this.name = name;
    this.price = price;
    this.quantity = quantity;
  }

  total() {
    return this.price * this.quantity;
  }

  get isLow() {
    return this.quantity < LOW_STOCK;
  }
}

function area(radius) {
  if (radius < 0) {
    throw new Error("radius must be positive");
  }
  return Math.PI * radius ** 2;
}

function parseLine(line) {
  const parts = line.split(",");
  if (parts.length !== 3) {
    return null;
  }
  const [name, price, quantity] = parts;
  return new Item(name.trim(), parseFloat(price), parseInt(quantity, 10));
}

function loadItems(lines) {
  const items = [];
  for (const line of lines) {
    if (!line.trim()) {
      continue;
    }
    const item = parseLine(line);
    if (item === null) {
      console.log("skipping bad line:", line);
      continue;
    }
    items.push(item);
  }
  return items;
}

function classify(item) {
  switch (item.quantity) {
    case 0:
      return "out of stock";
    default:
      return item.isLow ? "low" : "ok";
  }
}

function report(items) {
  let total = 0;
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const subtotal = item.total();
    if (item.isLow) {
      console.log(item.name, "is low");
    } else if (subtotal > 100) {
      console.log(item.name, "is a big line");
    } else {
      console.log(item.name, classify(item));
    }
    total += subtotal;
  }
  return total * (1 + TAX_RATE);
}

async function main() {
  const lines = ["bolt, 0.5, 100", "", "wheel, 30, 3", "broken line"];
  const items = loadItems(lines);
  let attempts = 0;
  let grandTotal = 0;
  while (attempts < 3) {
    attempts += 1;
    try {
      grandTotal = report(items);
      break;
    } catch (err) {
      console.error(err);
    } finally {
      console.log("attempt", attempts);
    }
  }
  do {
    attempts -= 1;
  } while (attempts > 0);
  console.log("grand total:", grandTotal.toFixed(2), "circle:", area(2));
}

main();
