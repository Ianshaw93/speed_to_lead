# Claude Code Instructions

## Test-Driven Development (TDD)

When implementing new features or fixing bugs, follow this TDD workflow:

### 1. Write Tests First
- Before writing any implementation code, write failing tests that define the expected behavior
- Tests should be clear, focused, and test one thing at a time
- Include edge cases and error conditions

### 2. Run Tests (Verify They Fail)
- Execute the test suite to confirm the new tests fail
- This validates that the tests are actually testing something meaningful

### 3. Write Implementation Code
- Write the minimum code necessary to make the tests pass
- Focus on making tests pass, not on perfect code

### 4. Run Tests (Verify They Pass)
- Execute the full test suite to ensure all tests pass
- If any tests fail, fix the implementation until all tests pass

### 5. Refactor (Optional)
- Once tests pass, refactor code for clarity and maintainability
- Re-run tests after refactoring to ensure nothing broke

## Key Principles

- Never consider a feature complete until tests pass
- Tests are documentation - write them to be readable
- When fixing bugs, write a test that reproduces the bug first
