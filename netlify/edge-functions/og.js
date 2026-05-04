export default async function handler(request, context) {
  const ua = request.headers.get("user-agent") || "";
  
  const isCrawler = 
    ua.includes("Twitterbot") ||
    ua.includes("facebookexternalhit") ||
    ua.includes("LinkedInBot") ||
    ua.includes("Slackbot") ||
    ua.includes("WhatsApp") ||
    ua.includes("Discordbot");

  if (!isCrawler) {
    return context.next();
  }

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>The Influence Registry: Who Funds Your Representatives?</title>
<meta property="og:type" content="website">
<meta property="og:url" content="https://keep-dc-honest.com">
<meta property="og:title" content="The Influence Registry: Who Funds Your Representatives?">
<meta property="og:description" content="Track AIPAC donations, fossil fuel money, pharma PACs, voting records and ethics scores for all 535 members of Congress. 100% public data.">
<meta property="og:image" content="https://keep-dc-honest.com/og-preview.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@keepdchonest">
<meta name="twitter:title" content="The Influence Registry: Who Funds Your Representatives?">
<meta name="twitter:description" content="Track AIPAC donations, fossil fuel money, pharma PACs, voting records and ethics scores for all 535 members of Congress. 100% public data.">
<meta name="twitter:image" content="https://keep-dc-honest.com/og-preview.png">
</head>
<body></body>
</html>`;

  return new Response(html, {
    headers: { "content-type": "text/html" }
  });
}

export const config = { path: "/" };
