/*
 *
 * This file demonstrates:
 *   - struct: grouping related variables together
 *   - typedef: giving a shorter name to a type
 *   - enum: defining named integer constants
 *   - Arrays of structs
 *   - Passing structs to functions (by value and by pointer)
 */

#include <stdio.h>
#include <string.h>  /* for strcpy */

#define NAME_LEN 50
#define MAX_STUDENTS 3

/* enum GradeLevel:
 * Each name is associated with an integer value.
 * If you don't assign values, they start at 0 by default.
 * Here we explicitly start FRESHMAN at 1.
 */
enum GradeLevel {
    FRESHMAN = 1,
    SOPHOMORE,
    JUNIOR,
    SENIOR
};

/* Define a struct to represent a student */
typedef struct {
    char name[NAME_LEN];
    int id;
    double gpa;
    enum GradeLevel level;
} Student;

/* Function prototypes */
void print_student(const Student *s);
void promote_student(Student *s);

int main(void) {
    /* ---------- Single struct example ---------- */
    Student s1;

    strcpy(s1.name, "Alex");
    s1.id = 12345;
    s1.gpa = 3.8;
    s1.level = JUNIOR;

    printf("--- Single student example ---\n");
    print_student(&s1);

    printf("\nPromoting the student...\n");
    promote_student(&s1);
    print_student(&s1);

    /* ---------- Array of structs example ---------- */
    printf("\n--- Array of students example ---\n");

    Student class_list[MAX_STUDENTS];

    /* Fill the array manually here (in real programs, might read from user or file) */
    strcpy(class_list[0].name, "Taylor");
    class_list[0].id = 1001;
    class_list[0].gpa = 3.4;
    class_list[0].level = FRESHMAN;

    strcpy(class_list[1].name, "Jordan");
    class_list[1].id = 1002;
    class_list[1].gpa = 3.9;
    class_list[1].level = SENIOR;

    strcpy(class_list[2].name, "Morgan");
    class_list[2].id = 1003;
    class_list[2].gpa = 2.8;
    class_list[2].level = SOPHOMORE;

    /* Loop through array and print each student */
    for (int i = 0; i < MAX_STUDENTS; i++) {
        printf("\nStudent #%d:\n", i + 1);
        print_student(&class_list[i]);
    }

    return 0;
}

/* print_student:
 * Uses a pointer to Student so we don't copy the whole struct.
 * We use the '->' operator with a pointer to struct.
 */
void print_student(const Student *s) {
    printf("Name: %s\n", s->name);
    printf("ID:   %d\n", s->id);
    printf("GPA:  %.2f\n", s->gpa);

    printf("Level: ");
    switch (s->level) {
        case FRESHMAN:  printf("Freshman\n");  break;
        case SOPHOMORE: printf("Sophomore\n"); break;
        case JUNIOR:    printf("Junior\n");    break;
        case SENIOR:    printf("Senior\n");    break;
        default:        printf("Unknown\n");   break;
    }
}

/* promote_student:
 * Increases a student's GradeLevel by one, up to SENIOR.
 */
void promote_student(Student *s) {
    if (s->level < SENIOR) {
        s->level = s->level + 1;
    }
}
