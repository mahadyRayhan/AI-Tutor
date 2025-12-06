/*
 *
 * This file demonstrates:
 *   - Function prototypes, definitions, and calls
 *   - Parameters and return values
 *   - Pass-by-value (the default in C)
 *   - Simulated pass-by-reference using pointers
 *   - Local vs. global variables (scope)
 */

#include <stdio.h>

/* Global variable: visible to all functions in this file
 * (Not always good style, but useful to demonstrate scope.)
 */
int global_counter = 0;

/* Function prototypes */
int add(int a, int b);
double average_of_three(int a, int b, int c);
void increment_local(int x);
void increment_by_pointer(int *x);
void demonstrate_scope(void);

int main(void) {
    int x = 5;
    int y = 10;
    int z = 20;

    printf("--- Function calls with return values ---\n");
    int sum_xy = add(x, y);
    printf("add(%d, %d) = %d\n", x, y, sum_xy);

    double avg = average_of_three(x, y, z);
    printf("average_of_three(%d, %d, %d) = %.2f\n\n", x, y, z, avg);

    printf("--- Pass-by-value example ---\n");
    printf("Before increment_local, x = %d\n", x);
    increment_local(x);  /* x is passed by value, so function gets a copy */
    printf("After increment_local, x = %d (unchanged)\n\n", x);

    printf("--- Simulated pass-by-reference using pointers ---\n");
    printf("Before increment_by_pointer, x = %d\n", x);
    increment_by_pointer(&x);  /* pass the *address* of x */
    printf("After increment_by_pointer, x = %d (changed)\n\n", x);

    printf("--- Scope: global vs local variables ---\n");
    demonstrate_scope();
    demonstrate_scope();
    demonstrate_scope();

    return 0;
}

/* add:
 * Takes two integers and returns their sum.
 * 'a' and 'b' are **parameters** (local variables inside the function).
 */
int add(int a, int b) {
    int result = a + b;
    return result;  /* send the result back to the caller */
}

/* average_of_three:
 * Computes the average of three ints as a double.
 */
double average_of_three(int a, int b, int c) {
    int sum = a + b + c;
    double avg = sum / 3.0;  /* 3.0 forces floating-point division */
    return avg;
}

/* increment_local:
 * Demonstrates pass-by-value.
 * The parameter x is a COPY of the caller's variable.
 * Changing x here does NOT change the variable in main.
 */
void increment_local(int x) {
    printf("  [increment_local] x before = %d\n", x);
    x = x + 1;
    printf("  [increment_local] x after  = %d\n", x);
}

/* increment_by_pointer:
 * Simulates pass-by-reference using a pointer.
 * Parameter 'x' is a pointer to int (int *x).
 * *x means "the thing that x points to" (the original variable).
 */
void increment_by_pointer(int *x) {
    printf("  [increment_by_pointer] *x before = %d\n", *x);
    *x = *x + 1;  /* modify the original variable via the pointer */
    printf("  [increment_by_pointer] *x after  = %d\n", *x);
}

/* demonstrate_scope:
 * Shows the difference between a local variable and a global variable.
 */
void demonstrate_scope(void) {
    /* Local variable: only exists inside this function, resets each call */
    int local_counter = 0;

    local_counter++;
    global_counter++;

    printf("  local_counter = %d, global_counter = %d\n",
           local_counter, global_counter);
}
