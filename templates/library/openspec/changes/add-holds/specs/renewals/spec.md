## ADDED Requirements

### Requirement: A loan of a held title is not renewed

The library MUST refuse to renew a loan while another member holds its title.

#### Scenario: Renewing while another member holds the title is refused

- **WHEN** a member renews a loan of a title another member holds
- **THEN** it is refused as `title-held`
