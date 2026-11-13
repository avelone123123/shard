# Inference Safety and Process Verification

## Guard Protocol
- Streaming endpoints verify active backend process readiness.
- Missing or crashed backend triggers immediate graceful error.
- Eliminates phantom hang states during network disconnects.
