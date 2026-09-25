"""Configurable approval engine (docs/04-approval-engine.md).

One engine for every document that needs sign-off. It knows nothing about
purchase requests or payments: a module opts in by registering an
`ApprovalDocumentHandler` for its doc type and calling `engine.submit`.
"""
