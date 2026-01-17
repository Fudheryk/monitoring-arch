#!/usr/bin/env python3
"""
CLI pour valider les variables d'environnement du projet.
Vérifie si chaque variable dans .env.production est réellement utilisée.

Usage:
    python check_env.py [.env.production]
"""

import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple
from dataclasses import dataclass
from collections import defaultdict


@dataclass
class EnvVarUsage:
    """Information sur l'utilisation d'une variable d'environnement."""
    var_name: str
    defined_in_env: bool
    used_in: List[Tuple[str, int, str]]  # (fichier, ligne, contexte)
    
    @property
    def is_used(self) -> bool:
        return len(self.used_in) > 0
    
    @property
    def usage_summary(self) -> str:
        if not self.is_used:
            return "❌ NON UTILISÉE"
        files = {f[0] for f in self.used_in}
        return f"✅ Utilisée dans {len(files)} fichier(s)"


def parse_env_file(env_path: Path) -> Dict[str, str]:
    """Parse un fichier .env et retourne les variables."""
    variables = {}
    
    if not env_path.exists():
        print(f"❌ Fichier {env_path} introuvable")
        return variables
    
    with open(env_path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            
            # Ignorer commentaires et lignes vides
            if not line or line.startswith('#'):
                continue
            
            # Parse KEY=VALUE
            if '=' in line:
                key = line.split('=', 1)[0].strip()
                # Ignorer les références à d'autres variables
                if not key.startswith('${'):
                    variables[key] = f"line {line_num}"
    
    return variables


def search_var_in_files(var_name: str, search_paths: List[Path]) -> List[Tuple[str, int, str]]:
    """Cherche une variable dans les fichiers du projet."""
    usages = []
    
    # Patterns de recherche
    patterns = [
        rf'\$\{{{var_name}\}}',  # ${VAR}
        rf'\${var_name}(?![A-Z_])',  # $VAR (mais pas $VAR_OTHER)
        rf'os\.getenv\(["\']?{var_name}["\']?\)',  # os.getenv("VAR")
        rf'os\.environ\[["\']?{var_name}["\']?\]',  # os.environ["VAR"]
        rf'{var_name}\s*[:=]',  # VAR: ou VAR= dans YAML/docker-compose
        rf'["\']?{var_name}["\']?\s*:',  # "VAR": dans YAML
    ]
    
    combined_pattern = '|'.join(patterns)
    regex = re.compile(combined_pattern)
    
    # Extensions à chercher
    extensions = {'.py', '.yml', '.yaml', '.sh', '.conf', '.ini', '.json', '.js', '.ts', '.tsx'}
    
    for search_path in search_paths:
        if not search_path.exists():
            continue
            
        for file_path in search_path.rglob('*'):
            # Skip certains dossiers
            if any(p in file_path.parts for p in ['.git', 'node_modules', '__pycache__', '.venv', 'venv']):
                continue
            
            if file_path.is_file() and file_path.suffix in extensions:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                # Nettoyer la ligne pour l'affichage
                                context = line.strip()[:80]
                                rel_path = file_path.relative_to(search_path.parent)
                                usages.append((str(rel_path), line_num, context))
                except (UnicodeDecodeError, PermissionError):
                    pass
    
    return usages


def main():
    """Point d'entrée principal."""
    
    # Chemin du fichier .env
    env_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.env.production')
    
    if not env_file.exists():
        print(f"❌ Fichier {env_file} introuvable")
        print(f"Usage: python {sys.argv[0]} [chemin/.env.production]")
        sys.exit(1)
    
    print(f"📋 Analyse de: {env_file}")
    print(f"🔍 Recherche dans: {env_file.parent}")
    print("=" * 80)
    
    # Parser le fichier .env
    env_vars = parse_env_file(env_file)
    print(f"\n✅ {len(env_vars)} variables trouvées dans {env_file.name}\n")
    
    # Chemins de recherche
    project_root = env_file.parent
    search_paths = [
        project_root / 'server',
        project_root / 'webapp',
        project_root / 'docker',
        project_root / 'scripts',
    ]
    
    # Analyser chaque variable
    results = []
    unused_vars = []
    
    for var_name, defined_at in env_vars.items():
        usages = search_var_in_files(var_name, search_paths)
        usage = EnvVarUsage(var_name, True, usages)
        results.append(usage)
        
        if not usage.is_used:
            unused_vars.append(var_name)
    
    # Afficher les résultats
    print("\n" + "=" * 80)
    print("RÉSULTATS")
    print("=" * 80 + "\n")
    
    # Grouper par statut
    used = [r for r in results if r.is_used]
    unused = [r for r in results if not r.is_used]
    
    # Variables utilisées
    if used:
        print(f"✅ Variables UTILISÉES ({len(used)}):")
        print("-" * 80)
        for usage in sorted(used, key=lambda x: x.var_name):
            files = sorted(set(f[0] for f in usage.used_in))
            print(f"\n  {usage.var_name}")
            for file_path in files:
                occurrences = [f for f in usage.used_in if f[0] == file_path]
                print(f"    📄 {file_path} ({len(occurrences)} occurrence(s))")
    
    # Variables NON utilisées
    if unused:
        print(f"\n\n❌ Variables NON UTILISÉES ({len(unused)}):")
        print("-" * 80)
        for usage in sorted(unused, key=lambda x: x.var_name):
            print(f"  ❌ {usage.var_name}")
        
        print("\n💡 Ces variables peuvent être:")
        print("  1. Inutiles → à supprimer")
        print("  2. Définies en dur dans docker-compose → à déplacer dans .env")
        print("  3. Utilisées de manière non-standard → vérifier manuellement")
    
    # Résumé
    print("\n" + "=" * 80)
    print("RÉSUMÉ")
    print("=" * 80)
    print(f"  Total variables: {len(results)}")
    print(f"  ✅ Utilisées: {len(used)}")
    print(f"  ❌ Non utilisées: {len(unused)}")
    
    # Exit code
    if unused:
        print(f"\n⚠️  Nettoyer {len(unused)} variable(s) inutile(s)")
        sys.exit(1)
    else:
        print("\n✅ Toutes les variables sont utilisées !")
        sys.exit(0)


if __name__ == '__main__':
    main()
