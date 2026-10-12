const ITEMS = [
  "Person names", "Emails", "Phone numbers", "Credit cards", "CVV and expiry", "IBAN", "SWIFT / BIC", "ABA routing",
  "Bank accounts", "Crypto wallets", "US SSN", "Tax IDs", "Medical record numbers", "API keys", "JWT and bearer tokens",
  "Private keys (PEM)", "Passwords", "Connection strings", "Cloud secrets", "Recovery codes", "Internal URLs",
  "Internal hostnames", "Private IPs", "Addresses", "Dates of birth", "Custom deny-terms",
];

export function Marquee() {
  const row = [...ITEMS, ...ITEMS];
  return (
    <section aria-label="Entities Doppel detects" className="relative border-y border-[var(--border)] bg-[var(--surface)]/50 py-5">
      <div
        className="marquee-wrap overflow-hidden"
        style={{ WebkitMaskImage: "linear-gradient(90deg, transparent, #000 10%, #000 90%, transparent)", maskImage: "linear-gradient(90deg, transparent, #000 10%, #000 90%, transparent)" }}
      >
        <div className="marquee gap-3">
          {row.map((t, i) => (
            <span key={i} className="mr-3 flex items-center gap-2 whitespace-nowrap rounded-full border border-[var(--border-strong)] bg-white/[0.02] px-4 py-1.5 text-[13px] text-[var(--body)]">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--primary)]" />
              {t}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}
