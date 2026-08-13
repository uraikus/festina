function square(x) {
  return x * x;
}

console.log("=== basics demo ===");

let total = 0;
for (let i = 1; i <= 5; i++) {
  if (i % 2 === 0) {
    console.log(i, "is even, square =", square(i));
  } else {
    console.log(i, "is odd");
  }
  total = total + i;
}
console.log("total:", total);

let nums = [5, 3, 8, 1, 9];
let doubled = nums.map(n => n * 2);
let big = nums.filter(n => n > 4);
console.log("nums:", nums);
console.log("doubled:", doubled);
console.log("big:", big);
console.log("joined:", nums.join(" - "));
console.log("length:", nums.length);

let person = { name: "Ada", age: 36 };
console.log("person:", person);
console.log("name prop:", person.name);
console.log("keys:", Object.keys(person));

let s = "Hello, World";
console.log("upper:", s.toUpperCase());
console.log("slice:", s.slice(7, 12));
console.log("indexOf World:", s.indexOf("World"));
console.log("includes Hello:", s.includes("Hello"));

let pi = 3.14159;
console.log("toFixed:", pi.toFixed(2));
