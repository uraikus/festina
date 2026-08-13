# Festina

**A fast, modern programming language with the simplicity of JavaScript and the power of native compilation.**

Festina is a new programming language designed to make native application development approachable without sacrificing performance.

It takes the syntax developers already know from JavaScript and combines it with static typing, LLVM compilation, built-in databases, graphics, audio, and native executables.

> **Write code that feels familiar. Compile code that runs fast.**

[GitHub Repository](https://github.com/uraikus/festina)

## Why Festina?

Modern programming often forces developers to choose between convenience and performance.

JavaScript is remarkably productive, but its dynamic nature and runtime model are not ideal for every application. Systems languages provide excellent performance, but often require considerably more complexity.

Festina aims for a different balance.

```text
JavaScript-like syntax
        +
Static typing
        +
LLVM native compilation
        +
Built-in application features
        =
Fast, approachable native software
```

Festina is designed to feel familiar from the first line of code.

---

## A Familiar Language

If you know JavaScript, much of Festina will look immediately recognizable.

```festina
text func greet(name:text) {
    log(`Hello, ${name}!`)
}

greet('World')
```

You get familiar features such as:

* String interpolation
* Ternary expressions
* Arrays
* Objects through structs
* Property access
* `Date`
* Simple event handlers

But Festina removes many of JavaScript's dynamic behaviors in favor of predictable, statically typed code.

---

## Native Performance

Festina programs compile through LLVM into native executables.

There is no requirement for a JavaScript engine or interpreter to run your application.

The goal is simple:

**Write approachable code and ship a real native application.**

Festina is designed with performance in mind from the language level upward, including efficient memory management, minimal runtime overhead, and aggressive compiler optimization.

---

## Batteries Included

Festina is designed for applications, not just algorithms.

Common functionality is built directly into the language and runtime.

### SQLite

Database access is built in.

```festina
table users {
    id:int
    name:text
}

arr[users] people = sqlite('select * from users')
```

No database framework or configuration ceremony is required for basic applications.

### Graphics

Create graphical applications with simple global functions:

```festina
drawRect(0, 0, 100, 100)
drawCircle(50, 50, 25)
drawText('Hello Festina', 20, 20)
```

### Images

```festina
img logo = loadImage('logo.png')

drawImage(logo, 0, 0)
```

### Audio

```festina
aud music = loadAudio('music.mp3')

music.play()
```

### Events

Interactive applications can respond to events directly:

```festina
on click(x:int, y:int) {
    log(`Clicked at ${x}, ${y}`)
}
```

The goal is to make common application development feel straightforward rather than requiring a large collection of libraries and frameworks.

---

## Simple Types

Festina uses explicit types without requiring excessive ceremony.

```festina
int age = 32
text name = 'Patrick'
bool active = true
float score = 98.5
```

Arrays are typed:

```festina
arr[text] names = ['Patrick', 'John', 'Mary']
```

Structs provide familiar object-like data:

```festina
struct User {
    id:int
    name:text
}

User user
user.id = 1
user.name = 'Patrick'
```

The result is code that remains easy to read while giving the compiler much more information to optimize.

---

## No JavaScript Runtime Required

Festina is inspired by JavaScript without depending on JavaScript to execute.

A Festina application is compiled into a native executable.

```text
main.f
   ↓
Festina
   ↓
LLVM
   ↓
Native Executable
```

That means Festina is intended to be suitable for applications that need to be distributed as standalone native software.

---

## Simple Imports

Festina keeps project organization straightforward.

```festina
import database.f
import ui.f
import utilities.f
```

Imports are resolved at compile time and combined into the application.

There is no complicated runtime module system to configure.

Festina automatically prevents the same source file from being included more than once.

---

## No Boilerplate `main()`

A simple Festina program can simply contain executable code:

```festina
log('Hello World')
```

You don't need to create a `main()` function just to get started.

Festina automatically creates the program entry point when compiling the application's entry file.

---

## One Tool

The compiler is distributed as:

```bash
festina
```

Compile an application:

```bash
festina main.f
```

Specify an output name:

```bash
festina main.f -o myapp
```

The intention is for `festina` to eventually become a complete development tool for building, running, checking, formatting, and distributing Festina applications.

---

## Built to Grow

Festina is being designed with a long-term goal of becoming self-hosting.

The initial compiler can be implemented using an established systems language and LLVM.

Eventually, Festina should be capable of compiling the Festina compiler itself.

The ultimate goal is simple:

> **Festina written in Festina, compiled by Festina.**

---

## What Festina Is For

Festina is particularly well suited to applications where you want:

* Native executables
* Good performance
* Simple syntax
* Built-in databases
* Graphical interfaces
* Multimedia
* Event-driven applications
* A small and approachable language
* Less boilerplate than traditional systems programming

It is intended to occupy the space between highly dynamic application languages and lower-level systems languages.

---

## Project Status

Festina is under active development.

The language and compiler are evolving, and some features described here may not yet be available in the current release.

The project is currently focused on establishing the language, compiler, native code generation, runtime, and core application features.

Contributions, experimentation, and feedback are welcome.

## License

Festina is released under the MIT License.
