const Database = require('better-sqlite3');
const { createCanvas } = require('canvas');

console.log("=== sqlite demo ===");
const db = new Database(':memory:');
db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)");

const insert = db.prepare("INSERT INTO users (name, age) VALUES (?, ?)");
insert.run("Ada", 36);
insert.run("Grace", 85);
insert.run("Alan", 41);

const getByName = db.prepare("SELECT * FROM users WHERE name = ?");
const ada = getByName.get("Ada");
console.log("found:", ada.name, ada.age);

const all = db.prepare("SELECT * FROM users ORDER BY age");
const rows = all.all();
for (let i = 0; i < rows.length; i++) {
  console.log(rows[i].name, "is", rows[i].age);
}
db.close();

console.log("=== canvas demo ===");
const canvas = createCanvas(200, 100);
const ctx = canvas.getContext('2d');

ctx.fillStyle = "#3366ff";
ctx.fillRect(10, 10, 80, 50);

ctx.fillStyle = "red";
ctx.beginPath();
ctx.arc(150, 50, 30, 0, 6.28318);
ctx.fill();

ctx.strokeStyle = "black";
ctx.lineWidth = 3;
ctx.beginPath();
ctx.moveTo(0, 90);
ctx.lineTo(200, 90);
ctx.stroke();

canvas.toBuffer("./output.png");
console.log("wrote output.png");
