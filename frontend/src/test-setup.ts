// Load before any Angular module: makes the JIT compiler available so
// partial-compiled libraries (e.g. @angular/common) can finish compiling
// when they're imported in tests, and gives us zone.js for any code that
// reaches for it.
import 'zone.js';
import '@angular/compiler';
