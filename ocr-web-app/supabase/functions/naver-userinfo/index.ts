const NAVER_USERINFO_URL = "https://openapi.naver.com/v1/nid/me";

function json(body: Record<string, string | boolean>, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

Deno.serve(async (request) => {
  const authorization = request.headers.get("authorization");
  if (!authorization?.toLowerCase().startsWith("bearer ")) {
    return json({ error: "NAVER access token is required." }, 401);
  }

  let naverResponse: Response;
  try {
    naverResponse = await fetch(NAVER_USERINFO_URL, {
      headers: { Authorization: authorization },
    });
  } catch {
    return json({ error: "NAVER user information could not be retrieved." }, 502);
  }

  if (!naverResponse.ok) {
    return json({ error: "NAVER access token is invalid." }, 401);
  }

  const payload = await naverResponse.json();
  const profile = payload?.resultcode === "00" ? payload.response : null;
  if (!profile?.id) {
    return json({ error: "NAVER user information is unavailable." }, 401);
  }

  const email = String(profile.email ?? "").trim();
  if (!email) {
    return json({ error: "NAVER email consent is required." }, 422);
  }

  const id = String(profile.id);
  const name = String(profile.name || profile.nickname || email);
  return json({ sub: id, id, email, email_verified: true, name, full_name: name });
});
