# Add to Follow-up List

Add a prospect to the HeyReach follow-up list (511495) with custom follow-up messages.

## Arguments
$ARGUMENTS - The LinkedIn profile URL

## Workflow

1. **User provides in their message:**
   - LinkedIn profile URL (required)
   - FOLLOW_UP1 message (required) - preserve line breaks exactly as given
   - FOLLOW_UP2 message (optional) - default: "Would it even make sense for you to get clients on here? LI is not always a good fit"
   - FOLLOW_UP3 message (optional) - leave empty if not provided

2. **Extract name from the profile URL or message:**
   - First name from "Hey [Name]" in the message
   - Last name from profile URL slug if possible (e.g., `catherine-long-prosci...` → Long)

3. **Add to HeyReach list 511495:**
   ```bash
   curl -s -X POST "https://api.heyreach.io/api/public/list/AddLeadsToListV2" \
     -H "X-API-KEY: D2IMEWXJKSJlwwr8uMFIj2CrmC41U6fI+GStr92pN04=" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json" \
     -d '{
       "listId": 511495,
       "leads": [{
         "profileUrl": "<linkedin_url>",
         "firstName": "<first_name>",
         "lastName": "<last_name>",
         "customUserFields": [
           {"name": "FOLLOW_UP1", "value": "<message with \\n for line breaks>"},
           {"name": "FOLLOW_UP2", "value": "<message or default>"},
           {"name": "FOLLOW_UP3", "value": "<message or empty>"}
         ]
       }]
     }'
   ```

4. **Confirm success** - show addedLeadsCount from response

## Important Notes
- Profile URL must be public format: `linkedin.com/in/username` (NOT the `ACoAA...` Sales Nav IDs)
- Preserve line breaks in messages using `\n` in the JSON
- Escape single quotes with `'\''` in bash
