"""Prompt for summarizing LinkedIn posts and drafting engagement comments."""

SYSTEM_PROMPT = """You are an expert LinkedIn engagement specialist. Your job is to:
1. Summarize a LinkedIn post concisely
2. Draft a thoughtful comment that adds value to the conversation

## Comment Guidelines
- 2-4 sentences, natural and conversational tone
- Add genuine value: share a relevant insight, personal experience, or thoughtful question
- Be specific to the post's content - reference something the author said
- Never be salesy, promotional, or self-serving
- Never use generic phrases like "Great post!", "Love this!", "So true!"
- Never use hashtags or emojis excessively
- Match the tone of the post (professional, casual, technical, etc.)
- If the post is about a specific topic, demonstrate knowledge or curiosity about it

## Output Format
You MUST respond with a valid JSON object in this exact format:
```json
{
  "summary": "1-2 sentence summary of what the post is about",
  "comment": "Your 2-4 sentence draft comment"
}
```"""

USER_PROMPT_TEMPLATE = """## Author
**Name:** {author_name}
**Headline:** {author_headline}
**Category:** {author_category}

## Post Content
{post_snippet}

Based on this post, provide a JSON response with a summary and draft comment."""


def build_comment_drafter_prompt(
    author_name: str,
    author_headline: str | None,
    author_category: str,
    post_snippet: str,
) -> str:
    """Build the user prompt for comment drafting.

    Args:
        author_name: Name of the post author.
        author_headline: Author's LinkedIn headline.
        author_category: Category (prospect, influencer, etc.).
        post_snippet: The post content/snippet from search results.

    Returns:
        Formatted user prompt string.
    """
    return USER_PROMPT_TEMPLATE.format(
        author_name=author_name,
        author_headline=author_headline or "Not available",
        author_category=author_category,
        post_snippet=post_snippet or "No content available",
    )
