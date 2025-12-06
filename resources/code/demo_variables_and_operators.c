/*
 *
 * This file demonstrates:
 *   - Basic variable types: int, double, char
 *   - Declaring and initializing variables
 *   - Arithmetic operators: +, -, *, /, %
 *   - Integer division vs. floating-point division
 *   - Compound assignment (+=, -=, etc.)
 *   - Increment and decrement operators: ++, --
 *   - Relational operators: ==, !=, <, <=, >, >=
 *   - Logical operators: && (AND), || (OR), ! (NOT)
 *
 * The goal is to show *small*, understandable examples,
 * and explain what each line is doing.
 */

#include <stdio.h>

int main(void) {
    /* ---------- 1. Declaring variables and basic types ---------- */

    /* 'int' stores whole numbers (positive, negative, or zero) */
    int age = 20;          /* declaration + initialization */
    int year = 2025;

    /* 'double' stores real numbers (with decimal part) */
    double price = 19.99;  /* declaration + initialization */
    double tax_rate = 0.07;

    /* 'char' stores a single character (in single quotes) */
    char grade = 'A';

    printf("age = %d, year = %d\n", age, year);
    printf("price = %.2f, tax_rate = %.2f\n", price, tax_rate);
    printf("grade = %c\n\n", grade);

    /* ---------- 2. Arithmetic operators (+, -, *, /, %) ---------- */

    int a = 7;
    int b = 3;

    int sum        = a + b;  /* addition */
    int difference = a - b;  /* subtraction */
    int product    = a * b;  /* multiplication */
    int quotient   = a / b;  /* integer division (7 / 3 = 2) */
    int remainder  = a % b;  /* remainder (7 % 3 = 1) */

    printf("Given a = %d, b = %d:\n", a, b);
    printf("  a + b = %d\n", sum);
    printf("  a - b = %d\n", difference);
    printf("  a * b = %d\n", product);
    printf("  a / b = %d (integer division)\n", quotient);
    printf("  a %% b = %d (remainder)\n\n", remainder);

    /* ---------- 3. Integer vs. double division ---------- */

    int x = 5;
    int y = 2;

    /* Both x and y are ints, so 5 / 2 = 2 (integer division) */
    int int_div = x / y;

    /* We can force floating-point division by using a double */
    double float_div1 = x / (double)y;  /* cast y to double */
    double float_div2 = (double)x / y;  /* cast x to double */

    printf("Integer division: 5 / 2 = %d\n", int_div);
    printf("Floating division: 5 / 2 = %.2f\n", float_div1);
    printf("Floating division (other way): 5 / 2 = %.2f\n\n", float_div2);

    /* ---------- 4. Compound assignment operators ---------- */

    int counter = 10;

    /* These are short forms for updating a variable in place */
    counter += 5;  /* same as counter = counter + 5; */
    counter -= 3;  /* same as counter = counter - 3; */
    counter *= 2;  /* same as counter = counter * 2; */
    counter /= 4;  /* same as counter = counter / 4; */

    printf("After compound assignments, counter = %d\n\n", counter);

    /* ---------- 5. Increment and decrement operators ---------- */

    int i = 0;

    /* Post-increment: use i, then increase it by 1 */
    printf("i (initial) = %d\n", i);
    printf("i++ returns %d\n", i++);  /* prints 0, then i becomes 1 */
    printf("Now i = %d\n", i);

    /* Pre-increment: first increase i, then use it */
    printf("++i returns %d\n", ++i);  /* i becomes 2, then prints 2 */
    printf("Now i = %d\n\n", i);

    /* Similarly for decrement (--) */
    int j = 5;
    printf("j (initial) = %d\n", j);
    printf("j-- returns %d\n", j--);  /* prints 5, then j becomes 4 */
    printf("Now j = %d\n", j);
    printf("--j returns %d\n", --j);  /* j becomes 3, then prints 3 */
    printf("Now j = %d\n\n", j);

    /* ---------- 6. Relational operators ---------- */

    int m = 10;
    int n = 20;

    /* Relational operators return 1 (true) or 0 (false) in C */
    printf("m = %d, n = %d\n", m, n);
    printf("m == n is %d\n", (m == n));  /* equal to? */
    printf("m != n is %d\n", (m != n));  /* not equal to? */
    printf("m < n  is %d\n", (m < n));
    printf("m <= n is %d\n", (m <= n));
    printf("m > n  is %d\n", (m > n));
    printf("m >= n is %d\n\n", (m >= n));

    /* ---------- 7. Logical operators ---------- */

    int is_sunny = 1;  /* 1 means true */
    int is_warm  = 0;  /* 0 means false */

    /* && (AND) is true only if both sides are true */
    int go_for_walk = is_sunny && is_warm;

    /* || (OR) is true if at least one side is true */
    int open_window = is_sunny || is_warm;

    /* ! (NOT) flips true/false */
    int stay_inside = !is_sunny;

    printf("is_sunny = %d, is_warm = %d\n", is_sunny, is_warm);
    printf("is_sunny && is_warm = %d (go_for_walk)\n", go_for_walk);
    printf("is_sunny || is_warm = %d (open_window)\n", open_window);
    printf("!is_sunny          = %d (stay_inside)\n", stay_inside);

    return 0;
}
