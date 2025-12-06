/*
 *
 * This file demonstrates:
 *   - Declaring and using arrays (int array[])
 *   - Iterating over arrays with loops
 *   - Simple algorithms on arrays: sum, min, max
 *   - Strings as arrays of char ending with '\0'
 *   - Inputting strings with scanf and fgets
 *   - Common <string.h> functions: strlen, strcpy, strcat, strcmp
 */

#include <stdio.h>
#include <string.h>

#define ARRAY_SIZE   5
#define NAME_LEN    50
#define BUFFER_SIZE 80

/* Function prototypes */
void print_int_array(const int arr[], int size);
int sum_array(const int arr[], int size);
int min_array(const int arr[], int size);
int max_array(const int arr[], int size);
void demonstrate_strings(void);

int main(void) {
    /* ---------- 1. Arrays of integers ---------- */
    int numbers[ARRAY_SIZE] = {10, 20, 30, 40, 50};

    printf("--- Integer array example ---\n");
    print_int_array(numbers, ARRAY_SIZE);

    printf("Sum of array = %d\n", sum_array(numbers, ARRAY_SIZE));
    printf("Min of array = %d\n", min_array(numbers, ARRAY_SIZE));
    printf("Max of array = %d\n\n", max_array(numbers, ARRAY_SIZE));

    /* ---------- 2. Strings (arrays of char) ---------- */
    demonstrate_strings();

    return 0;
}

/* print_int_array:
 * Loops through array and prints each element.
 * 'const' means we promise not to modify the array inside this function.
 */
void print_int_array(const int arr[], int size) {
    printf("Array elements:\n");
    for (int i = 0; i < size; i++) {
        printf("  arr[%d] = %d\n", i, arr[i]);
    }
}

/* sum_array:
 * Adds up all the elements.
 */
int sum_array(const int arr[], int size) {
    int sum = 0;
    for (int i = 0; i < size; i++) {
        sum += arr[i];
    }
    return sum;
}

/* min_array:
 * Finds and returns the smallest element.
 */
int min_array(const int arr[], int size) {
    int min = arr[0];  /* start by assuming first element is smallest */
    for (int i = 1; i < size; i++) {
        if (arr[i] < min) {
            min = arr[i];  /* found a new smaller value */
        }
    }
    return min;
}

/* max_array:
 * Finds and returns the largest element.
 */
int max_array(const int arr[], int size) {
    int max = arr[0];
    for (int i = 1; i < size; i++) {
        if (arr[i] > max) {
            max = arr[i];
        }
    }
    return max;
}

/* demonstrate_strings:
 * Shows how strings work and uses several <string.h> functions.
 */
void demonstrate_strings(void) {
    char name[NAME_LEN];
    char buffer[BUFFER_SIZE];

    printf("--- String examples ---\n");

    /* Using scanf with %s reads a word (no spaces) */
    printf("Enter your first name (one word): ");
    scanf("%49s", name);  /* limit to 49 chars + '\0' */

    printf("You entered: %s\n", name);
    printf("Length of your name = %lu\n",
           (unsigned long)strlen(name));

    /* Clear leftover newline from input buffer before using fgets */
    int c;
    while ((c = getchar()) != '\n' && c != EOF) {
        /* Discard characters until end of line */
    }

    /* Using fgets to read a full line including spaces */
    printf("\nEnter your full name (may include spaces): ");
    if (fgets(buffer, BUFFER_SIZE, stdin) != NULL) {
        printf("You entered (with newline): %s", buffer);
    }

    /* strcpy: copy one string into another buffer */
    char copy[NAME_LEN];
    strcpy(copy, name);  /* copy contents of 'name' into 'copy' */
    printf("\nCopy of first name: %s\n", copy);

    /* strcat: concatenate strings (append) */
    char greeting[100] = "Hello, ";
    strcat(greeting, name);   /* greeting = "Hello, " + name */
    printf("%s!\n", greeting);

    /* strcmp: compare two strings.
     * Returns:
     *   0  if they are equal
     *  <0  if first is "less than" second
     *  >0  if first is "greater than" second
     */
    if (strcmp(name, "Alex") == 0) {
        printf("Your name is Alex (strcmp returned 0).\n");
    } else {
        printf("Your name is NOT Alex (strcmp returned %d).\n",
               strcmp(name, "Alex"));
    }
}
