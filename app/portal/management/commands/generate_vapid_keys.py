from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "Generate VAPID key pair for Web Push notifications"

    def handle(self, *args, **options):
        from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key, SECP256R1
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat, PrivateFormat, NoEncryption
        )
        import base64

        private_key = generate_private_key(SECP256R1())
        pem = private_key.private_bytes(
            Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
        ).decode().strip()
        pub_bytes = private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b'=').decode()

        # For .env, escape PEM newlines
        pem_oneline = pem.replace('\n', '\\n')

        self.stdout.write(self.style.SUCCESS("VAPID keys generated. Add to config/.env:\n"))
        self.stdout.write(f'VAPID_PRIVATE_KEY="{pem_oneline}"')
        self.stdout.write(f'VAPID_PUBLIC_KEY={pub_b64}')
        self.stdout.write(f'VAPID_SUBJECT=mailto:admin@zdrowa.pracunia.pl')
        self.stdout.write(self.style.WARNING("\nKEEP PRIVATE KEY SECRET. Store in .env only, never commit."))
