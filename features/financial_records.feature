Feature: Financial records acceptance for the Sprint 1 increment
  The current US01 increment registers transactions, and PF-02 provides a
  monthly summary. Budgets, authentication and alerts are outside this feature.

  Background:
    Given a separate demonstration user

  @US01 @create
  Scenario: Register an income without losing its amount
    When I register an income of "1000.00" with key "income-0001"
    Then the API responds with status 201
    And the movement amount is "1000.00"

  @US01 @idempotency
  Scenario: Retry a registration without creating a duplicate
    Given an income of "1000.00" registered with key "income-0001"
    When I repeat the same registration
    Then the API responds with status 200
    And the movement keeps its original identifier
    And the replay header is "true"

  @US01 @conflict
  Scenario: Reject a reused key with different financial content
    Given an income of "1000.00" registered with key "income-0001"
    When I register an income of "2000.00" with key "income-0001"
    Then the API responds with status 409

  @PF02 @summary
  Scenario: Calculate a monthly summary and isolate the demonstration users
    Given an income of "1000.00" registered with key "income-0001"
    And an expense of "125.50" registered with key "expense-0001"
    When I request the monthly summary for "2026-10"
    Then the API responds with status 200
    And income, expenses and balance are "1000.00", "125.50" and "874.50"
    And another demonstration user has no movements

  @validation
  Scenario: Reject an invalid calendar month
    When I request the monthly summary for "2026-13"
    Then the API responds with status 422

  @local @US01 @persistence
  Scenario: Preserve the identifier after recreating the local application
    Given an income of "1000.00" registered with key "income-0001"
    When I recreate the local application and repeat the registration
    Then the API responds with status 200
    And the movement keeps its original identifier
