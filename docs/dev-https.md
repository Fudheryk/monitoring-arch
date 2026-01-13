# Dev HTTPS (monitoring.local) + Windows + CentOS RPM

Ce projet expose **une seule URL** en dev :
- Web + API derrière un reverse proxy Nginx : **https://monitoring.local**
- Les services internes restent en HTTP sur le réseau Docker.

> ⚠️ Les certificats (clés privées) ne doivent jamais être commit.
> Le dossier `docker/certs/` est ignoré par git (sauf `.gitkeep`).

---

## 0) Pré-requis

### VM Debian (Docker host)
- docker + docker compose
- mkcert installé (binaire)
- libnss3-tools installé (pour certains navigateurs)

### PC Windows (poste dev)
- droits admin (édition hosts + trust store)
- mkcert installé

### VM CentOS 7.9 (tests RPM/agent)
- accès réseau vers la VM Debian
- curl (ou openssl)

---

## 1) VM Debian — créer le certificat et démarrer le stack

Depuis la racine du repo :

### 1.1 Générer le certificat TLS
```bash
mkcert -install
mkcert monitoring.local
````

### 1.2 Placer les fichiers dans le projet

```bash
mkdir -p docker/certs
mv monitoring.local.pem docker/certs/monitoring.local.pem
mv monitoring.local-key.pem docker/certs/monitoring.local-key.pem
```

### 1.3 Démarrer

```bash
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up -d --build
```

### 1.4 Vérifier (dans la VM Debian)

```bash
curl -vk https://monitoring.local/api/v1/health
curl -vk https://monitoring.local/ -o /dev/null
curl -vk https://monitoring.local/api/docs -o /dev/null
```

---

## 2) Windows — accéder à [https://monitoring.local](https://monitoring.local) sans alerte

### 2.1 Ajouter l’entrée hosts (ADMIN)

Éditer :
`C:\Windows\System32\drivers\etc\hosts`

Ajouter :

```
<IP_VM_DEBIAN> monitoring.local
```

Puis vider le cache DNS :

```powershell
ipconfig /flushdns
```

### 2.2 Installer mkcert sur Windows

Via winget (si dispo) :

```powershell
winget install FiloSottile.mkcert
```

Ou via Chocolatey :

```powershell
choco install mkcert -y
```

### 2.3 Installer la CA mkcert dans Windows

```powershell
mkcert -install
```

### 2.4 Générer un cert signé par la CA Windows (recommandé)

> Best practice pour éviter de “faire confiance” à une CA générée dans la VM.

```powershell
mkcert monitoring.local
```

Cela produit :

* `monitoring.local.pem`
* `monitoring.local-key.pem`

Copier ensuite ces 2 fichiers dans la VM Debian dans :

* `docker/certs/monitoring.local.pem`
* `docker/certs/monitoring.local-key.pem`

Puis redémarrer le proxy :

```bash
docker compose -f docker/docker-compose.yml restart proxy
```

### 2.5 Test navigateur

Ouvrir :

* [https://monitoring.local](https://monitoring.local)

---

## 3) CentOS 7.9 — tester l’agent RPM avec TLS (prod-like)

### 3.1 Ajouter l’entrée hosts (CentOS)

```bash
sudo sh -lc 'echo "<IP_VM_DEBIAN> monitoring.local" >> /etc/hosts'
```

### 3.2 Installer une CA de confiance (recommandé)

But : permettre à `curl` et à l’agent de vérifier TLS proprement.

Sur **Debian**, récupérer le chemin du CA mkcert :

```bash
mkcert -CAROOT
```

Copier `rootCA.pem` vers CentOS (exemple) :

```bash
scp "$(mkcert -CAROOT)/rootCA.pem" centos@<IP_CENTOS>:/tmp/monitoring-dev-ca.pem
```

Sur **CentOS** :

```bash
sudo cp /tmp/monitoring-dev-ca.pem /etc/pki/ca-trust/source/anchors/monitoring-dev-ca.pem
sudo update-ca-trust
```

### 3.3 Vérifier TLS depuis CentOS

```bash
curl https://monitoring.local/api/v1/health
```

### 3.4 Installer et lancer le RPM agent

```bash
sudo rpm -ivh monitoring-agent.rpm
sudo systemctl start monitoring-agent
sudo systemctl status monitoring-agent
journalctl -u monitoring-agent -f
```

---

## 4) Dépannage rapide

### 4.1 Certificat non valide dans le navigateur (Windows)

* vérifier que `mkcert -install` a bien été exécuté sur Windows
* vérifier que `monitoring.local` pointe vers l’IP de la VM Debian (hosts)
* vérifier que nginx utilise bien les fichiers cert attendus

### 4.2 Ports déjà utilisés

```bash
sudo ss -ltnp | egrep ':80 |:443 '
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

### 4.3 API docs / openapi

* API via proxy : [https://monitoring.local/api/docs](https://monitoring.local/api/docs)
* Webapp docs : [https://monitoring.local/docs](https://monitoring.local/docs)

````

