# Certificados intermedios

Algunos servidores del Estado mandan la cadena TLS incompleta. curl en Windows la completa solo (AIA),
pero Python no lo hace. Aquí van los intermedios que faltan. El Dockerfile los suma al paquete de
certifi y apunta `SSL_CERT_FILE` a esa unión. La verificación no se desactiva nunca.

| Archivo | Para | De dónde | Verificado |
|---|---|---|---|
| `sectigo_rsa_organization_validation.pem` | www.funcionpublica.gov.co (gestor normativo) | AIA de su certificado: `http://crt.sectigo.com/SectigoRSAOrganizationValidationSecureServerCA.crt` | 2026-10-22: `openssl verify` contra certifi da OK (firmado por USERTrust RSA). SHA-256 `72:A3:4A:C2:…:3E:C0`; vence el 2030-12-31 |

Cada intermedio nuevo entra con su prueba en `tests/test_web_mcp.py`: la huella fijada.
