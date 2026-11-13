"""SQL repository boundary.

Repository methods are added alongside their use cases in the next implementation phase.
All writes that consume an approval must be transactional and use SELECT ... FOR UPDATE.
"""
