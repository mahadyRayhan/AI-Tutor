/*
 *
 * This file demonstrates:
 *   - The FILE * type for file input/output
 *   - fopen (open a file), fclose (close a file)
 *   - fprintf (write formatted output to a file)
 *   - fscanf (read formatted input from a file)
 *   - Reading files in a loop until EOF (end-of-file)
 *
 * Example:
 *   1) Write some integers to a text file.
 *   2) Read them back and compute their sum.
 */

#include <stdio.h>
#include <stdlib.h>  /* for exit */

#define FILENAME "numbers.txt"

/* Function prototypes */
void write_integers_to_file(void);
int read_and_sum_integers_from_file(void);

int main(void) {
    printf("--- File processing example ---\n");

    write_integers_to_file();
    int sum = read_and_sum_integers_from_file();

    printf("Sum of integers in file '%s' = %d\n", FILENAME, sum);

    return 0;
}

/* write_integers_to_file:
 * Opens a file for writing ("w" mode).
 * Writes a few integers (one per line) using fprintf.
 * Closes the file.
 */
void write_integers_to_file(void) {
    FILE *fPtr = fopen(FILENAME, "w");  /* open for writing */

    if (fPtr == NULL) {
        printf("Error: could not open file '%s' for writing.\n", FILENAME);
        exit(1);  /* exit the program with error code 1 */
    }

    printf("Writing integers to file '%s'...\n", FILENAME);

    /* Write 5 integers to the file, one per line */
    for (int i = 1; i <= 5; i++) {
        fprintf(fPtr, "%d\n", i * 10);  /* 10, 20, 30, 40, 50 */
    }

    /* Always close the file when done */
    fclose(fPtr);
}

/* read_and_sum_integers_from_file:
 * Opens the file for reading ("r" mode).
 * Reads integers with fscanf until it reaches EOF.
 * Returns the sum of the integers.
 */
int read_and_sum_integers_from_file(void) {
    FILE *fPtr = fopen(FILENAME, "r");  /* open for reading */

    if (fPtr == NULL) {
        printf("Error: could not open file '%s' for reading.\n", FILENAME);
        exit(1);
    }

    int value;
    int sum = 0;

    /* fscanf returns the number of items successfully read.
     * If it returns 1 when using "%d", we successfully read an int.
     * When it fails (e.g., EOF), it returns EOF or 0.
     */
    while (fscanf(fPtr, "%d", &value) == 1) {
        sum += value;
    }

    fclose(fPtr);  /* close the file */
    return sum;
}
