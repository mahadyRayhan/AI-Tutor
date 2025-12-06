/*
 *
 * This file demonstrates:
 *   - Basic pointer concepts: addresses (&) and dereference (*)
 *   - Using pointers to simulate pass-by-reference (swap function)
 *   - Relationship between arrays and pointers
 *   - Dynamic memory allocation with malloc
 *   - Freeing memory with free
 */

#include <stdio.h>
#include <stdlib.h>  /* for malloc and free */

/* Function prototypes */
void basic_pointer_demo(void);
void swap(int *a, int *b);
double average_of_n_integers(int n);

int main(void) {
    basic_pointer_demo();

    printf("\n--- Swapping values with pointers ---\n");
    int x = 3;
    int y = 7;
    printf("Before swap: x = %d, y = %d\n", x, y);
    swap(&x, &y);  /* pass the addresses of x and y */
    printf("After swap:  x = %d, y = %d\n", x, y);

    printf("\n--- Dynamic memory with malloc and free ---\n");
    int n;
    printf("How many integers do you want to average? ");
    scanf("%d", &n);

    if (n > 0) {
        double avg = average_of_n_integers(n);
        printf("Average = %.2f\n", avg);
    } else {
        printf("n must be positive.\n");
    }

    return 0;
}

/* basic_pointer_demo:
 * Explains addresses and dereference using a small example.
 */
void basic_pointer_demo(void) {
    printf("--- Basic pointer demo ---\n");

    int value = 42;         /* a normal int variable */
    int *ptr = &value;      /* ptr is a pointer to int, store address of value */

    printf("value      = %d\n", value);
    printf("&value     = %p (address of value)\n", (void *)&value);
    printf("ptr        = %p (ptr holds the same address)\n", (void *)ptr);
    printf("*ptr       = %d (dereferencing ptr gives the value)\n", *ptr);

    /* If we modify *ptr, we change 'value' */
    *ptr = 99;
    printf("After *ptr = 99, value = %d\n", value);

    /* Arrays and pointers:
     * For an array arr, the expression arr (without index)
     * can be used as a pointer to its first element.
     */
    int arr[3] = {10, 20, 30};
    int *p = arr;  /* same as &arr[0] */

    printf("\nArray example:\n");
    printf("arr[0] = %d, *(p)   = %d\n", arr[0], *p);
    printf("arr[1] = %d, *(p+1) = %d\n", arr[1], *(p + 1));
    printf("arr[2] = %d, *(p+2) = %d\n", arr[2], *(p + 2));
}

/* swap:
 * Swaps the values of two integers using pointers.
 * This demonstrates pass-by-reference style in C.
 */
void swap(int *a, int *b) {
    int temp = *a;  /* temp holds value at address a */
    *a = *b;        /* copy value at b into the variable pointed by a */
    *b = temp;      /* copy temp into the variable pointed by b */
}

/* average_of_n_integers:
 * Demonstrates dynamic memory allocation:
 *   - We ask the user how many integers they want (n).
 *   - We allocate an array of n ints using malloc.
 *   - We read the values, compute the average, then free the memory.
 */
double average_of_n_integers(int n) {
    int *values = NULL;
    int sum = 0;

    /* malloc allocates n * sizeof(int) bytes of memory.
     * It returns a pointer to the memory block, or NULL if it fails.
     */
    values = (int *)malloc(n * sizeof(int));

    if (values == NULL) {
        printf("Memory allocation failed.\n");
        return 0.0;  /* Return some default value on error */
    }

    printf("Enter %d integers:\n", n);
    for (int i = 0; i < n; i++) {
        scanf("%d", &values[i]);  /* array indexing with the pointer */
        sum += values[i];
    }

    double average = sum / (double)n;

    /* When we are done with the dynamically allocated memory,
     * we must free it to avoid memory leaks.
     */
    free(values);
    values = NULL;  /* good practice: avoid dangling pointer */

    return average;
}
