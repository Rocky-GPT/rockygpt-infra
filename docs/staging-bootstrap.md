# Staging bootstrap checklist

1. Create an independent Neon project; do not branch or clone production data.
2. Create separate data-owner/data-runtime and brain-app credentials with the
   same schema boundaries documented in `application-isolation.md`.
3. Apply the data schema with the owner credential, grant the runtime credential
   read-only access, and allow the brain credential only on `rockygpt_brain`.
4. Run the role-isolation assertions before adding any service URL.
5. Restore the verified campus artifact using the staging data workflow. Leave
   brain chat and feedback tables empty.
6. Create distinct staging values for the service token, abuse/log hash keys,
   admin token, budgeted OpenAI key, and read-only artifact credentials.
7. Deploy data, then brain, then the Vercel staging preview. Configure the same
   service token in both Render services and as a server-only Vercel branch
   variable.
8. Enable Vercel Authentication for the staging preview and create a separate
   automation bypass secret for smoke workflows.
9. Run staging smoke and the manual model eval gate before the first promotion.
