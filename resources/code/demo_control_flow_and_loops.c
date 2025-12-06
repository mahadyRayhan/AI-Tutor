/*
 *
 * This file demonstrates:
 *   - if, if/else, if/else-if/else chains
 *   - switch statements
 *   - while loops
 *   - do...while loops
 *   - for loops
 *   - Counter-controlled loops vs sentinel-controlled loops
 *   - Nested loops (brief example)
 */

#include <stdio.h>

/* Function prototypes */
void demonstrate_if_else(int number);
void demonstrate_switch(char grade);
int sum_first_n(int n);
int sum_until_sentinel(void);
void multiplication_table(int rows, int cols);

int main(void) {
    int number;
    char grade;

    /* ----- IF / ELSE example ----- */
    printf("Enter an integer to test if/else: ");
    scanf("%d", &number);
    demonstrate_if_else(number);

    /* ----- SWITCH example ----- */
    printf("\nEnter your letter grade (A, B, C, D, F): ");
    scanf(" %c", &grade);  /* space before %c skips whitespace */
    demonstrate_switch(grade);

    /* ----- Counter-controlled loop (for) ----- */
    int n;
    printf("\nEnter n to sum from 1 to n: ");
    scanf("%d", &n);
    printf("Sum from 1 to %d is %d\n", n, sum_first_n(n));

    /* ----- Sentinel-controlled loop (while) ----- */
    printf("\nNow we will sum integers until you enter -1 (sentinel):\n");
    int sentinel_sum = sum_until_sentinel();
    printf("Sentinel-controlled sum is %d\n", sentinel_sum);

    /* ----- Nested loops: basic multiplication table ----- */
    printf("\nSmall multiplication table:\n");
    multiplication_table(3, 5);

    return 0;
}

/* demonstrate_if_else:
 * Shows basic if, if/else, and else-if chain.
 */
void demonstrate_if_else(int number) {
    printf("\n--- if / else examples ---\n");

    if (number > 0) {
        printf("%d is positive.\n", number);
    } else if (number < 0) {
        printf("%d is negative.\n", number);
    } else {
        printf("Number is zero.\n");
    }

    /* We can also combine conditions using logical operators */
    if (number >= 1 && number <= 10) {
        printf("%d is between 1 and 10 (inclusive).\n", number);
    } else {
        printf("%d is NOT between 1 and 10.\n", number);
    }
}

/* demonstrate_switch:
 * Shows switch/case/break for discrete options.
 */
void demonstrate_switch(char grade) {
    printf("\n--- switch example ---\n");

    switch (grade) {
        case 'A':
        case 'a':
            printf("Excellent work!\n");
            break;

        case 'B':
        case 'b':
            printf("Good job!\n");
            break;

        case 'C':
        case 'c':
            printf("You passed.\n");
            break;

        case 'D':
        case 'd':
            printf("You barely passed.\n");
            break;

        case 'F':
        case 'f':
            printf("Better luck next time.\n");
            break;

        default:
            printf("Unknown grade.\n");
            break;
    }
}

/* sum_first_n:
 * Uses a counter-controlled for loop to sum integers 1..n.
 */
int sum_first_n(int n) {
    int sum = 0;

    /* A for loop has three parts:
     *   1) initialization: i = 1
     *   2) condition: i <= n
     *   3) update: i++
     */
    for (int i = 1; i <= n; i++) {
        sum += i;  /* same as sum = sum + i; */
    }

    return sum;
}

/* sum_until_sentinel:
 * Uses a sentinel-controlled while loop.
 * The user keeps entering integers until they enter -1.
 */
int sum_until_sentinel(void) {
    int total = 0;
    int value;

    /* Priming read: we read before the loop to initialize 'value' */
    printf("Enter an integer (-1 to stop): ");
    scanf("%d", &value);

    /* While the sentinel has NOT been entered, keep looping */
    while (value != -1) {
        total += value;
        printf("Enter an integer (-1 to stop): ");
        scanf("%d", &value);
    }

    /* When value == -1, the loop stops */
    return total;
}

/* multiplication_table:
 * Simple example of nested for loops.
 * Prints a rows x cols table of multiplication results.
 */
void multiplication_table(int rows, int cols) {
    /* Outer loop: which row we are on */
    for (int r = 1; r <= rows; r++) {
        /* Inner loop: which column we are on */
        for (int c = 1; c <= cols; c++) {
            printf("%4d", r * c);  /* %4d prints in a field width of 4 */
        }
        printf("\n");
    }
}
