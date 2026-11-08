<!--
HOW TO READ THIS FILE (for humans, the app ignores this comment)

- Each heading is one section of the document. The app adds the heading itself.
- The text under a heading is the instruction for the AI agent.
- Lines starting with @ are small settings for the app:

  @type: owner        -> AI skips it. The app writes "To be filled by the project owner."
  @source: scanner    -> Mostly filled from the project files (dependency/config files).
  @write: last        -> Write this section after all the other sections.
  @after: 3.3         -> Write this section after section 3.3 is finished.
  @table: A | B | C   -> The answer is a table with these columns.
-->

# GLOBAL RULES
@type: rules

These rules apply to every section below.
- Write in formal, clear English. Use the third person and the present tense (example: "The system allows users to ...").
- Use only facts found in the project information you are given. Never invent features, names, versions, or URLs.
- If the information is missing or not clear, write "TBD".
- Follow the length limit of each section. Do not add extra headings, greetings, or closing sentences.
- Write only the content of the section. Do not repeat the section title.
- Always use the same function names, user roles, and IDs everywhere in the document.

# 1.0 Introduction
@write: last

Write 1-2 sentences introducing this chapter. Say that it covers the purpose, scope, terminology, references, and overview of the document.

## 1.1 Purpose
Write 2-3 sentences. Explain what the system is, why it exists, and who it is for.

## 1.2 System Scope
@table: Function | Description

List the main functions of the system. Write each description in 1 short sentence. Use the function names from the project's function list.

## 1.3 Terminology
@table: Term | Definition

List the special words, abbreviations, technologies, and user roles used in this document. Maximum 10 terms. Write each definition in 1 short sentence.

## 1.4 References
@source: scanner

List the main frameworks, libraries, and tools used to build the project (name and version if available). Take them only from the project's dependency files. Do not guess any website link. Write a URL only if it appears in the project files.

## 1.5 Overview
@write: last

Write 2 sentences. First, summarize what this chapter covered. Second, explain what the next chapters (General Description and Specific Requirements) contain.

# 2.0 General Description
@write: last

Write 1-2 sentences introducing this chapter. Say that it covers the product perspective, product functions, user characteristics, and general constraints.

## 2.1 Product Perspective
Write 1-2 sentences. Explain what kind of system this is (example: desktop app, web app), what it does, and the main technology it is built with.

## 2.2 Product Functions
Write a bullet list. For each function, write the function name, then 1-2 sentences explaining how it works from the user's point of view. Format: "Function name: explanation".

## 2.3 User Characteristics

### 2.3.1 Users Definition
Write 1 sentence stating who can use the system (the user roles). If there is only one type of user, say so.

### 2.3.2 User Access Function
@table: Function | (one column for each user role)

Make a table. The first column is the function name. Add one column for each user role. Put a check mark if that role can use or see the function. Put "-" if it cannot. If the roles are not clear, use one column called "User".

## 2.4 General Constraints

### 2.4.1 General Constraints
@table: CR_ID | Description

List 5-8 limits or rules the system must follow. Examples: platform limits, technology limits, offline use, license, performance limits. Write each description in 1 sentence. The app creates the CR_ID values (CR_01, CR_02, ...).

### 2.4.2 Dependencies
@source: scanner

Write a bullet list of what the user must have to run the app. Include: operating system, required software with versions, required accounts or API keys, and whether internet is needed. Take this from the project's dependency and configuration files.

# 3.0 Specific Requirements
@write: last

Write 1 sentence introducing this chapter. Say that it lists the detailed requirements of the system.

## 3.1 External Interface Requirements

### 3.1.1 System Interfaces
Write 1 sentence. Explain how users access the system (example: desktop application, web browser, mobile app) and which operating system or platform it runs on.

### 3.1.2 Hardware Interfaces
Write 1 sentence. Explain which hardware or devices the system supports or needs (example: keyboard, printer, minimum RAM, GPU). If the project does not say, write "TBD".

### 3.1.3 Communication Interfaces
Write 1 sentence. Explain which network communication the system uses (example: HTTP/HTTPS, a server, WebSocket, Bluetooth). If the system works fully offline, say so. If it is not clear in the project, write "TBD".

## 3.2 User Interfaces
@type: owner

## 3.3 Functional Requirements
@table: FR_ID | Functional Requirement

List every main function of the system. Write only the function name (example: Dashboard), with no description. Keep the names exactly the same as the project's function list. The app creates the FR_ID values (FR_01, FR_02, ...).

## 3.4 Use Cases
@type: owner

## 3.5 Structural Requirements
@source: scanner

Write a list of the main classes of the project (maximum 10). Format: "Class name: what it does (1 sentence). Related to: other class names." If the project does not use classes, write "TBD".

## 3.6 Behavioral Requirements
@type: owner

## 3.7 Design Constraints Requirements
@after: 3.3
@table: CR_ID | Description | Related FR_ID

Use exactly the same constraints as section 2.4.1. Do not change any CR_ID or description. Only fill the Related FR_ID column, using the FR_ID values from section 3.3 that the constraint affects. Separate many IDs with commas. If the constraint affects the whole system, write "All".

## 3.8 Quality Requirements
@after: 3.3
@table: QR_ID | Quality Requirements | Related FR_ID

List 4-8 quality requirements. A quality requirement describes what the system is expected to do in terms of quality, such as speed, security, reliability, or ease of use. Write each in 1 clear sentence (example: "The system shall ..."). Fill Related FR_ID with the FR_ID values from section 3.3 that it affects, separated by commas, or "All" if it affects the whole system. The app creates the QR_ID values (QR_01, QR_02, ...).
