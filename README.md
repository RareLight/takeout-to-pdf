# 📧 Takeout to PDF Converter 🔄📄

**Transform Google Takeout emails into organized, archival-quality PDFs with attachments**

[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

![Console Output](console_output.png)

## 📖 Table of Contents

- [Key Features](#-key-features)
- [Tech Stack](#-tech-stack)
- [Installation](#-installation)
- [Usage](#-usage)
- [Use Cases](#-use-cases)
- [Contributing](#-contributing)
- [License](#-license)

## 🚀 Key Features

| Feature                          | Description                                                     |
| -------------------------------- | --------------------------------------------------------------- |
| **Complete Email Preservation**  | Extract text bodies (plaintext/HTML), attachments, and metadata |
| **Smart Chronological Ordering** | Auto-sort by send/receive date with timezone awareness          |
| **PDF Professional Formatting**  | Clean typography with CSS styling and responsive images         |
| **Error Resilience**             | Skip corrupted emails while preserving 95%+ of data             |
| **Enterprise Scalability**       | Process 10,000+ email archives with progress tracking           |

## 🔧 Tech Stack

**Core Components**

```bash
📦 Python 3.10+
📚 weasyprint (PDF generation)
🔗 BeautifulSoup4 (HTML email processing)
🔄 mailbox (MBOX file parsing)
📊 tqdm (progress visualization)
```

**System Dependencies**

```bash
# Windows users require
GTK3 Runtime (for PDF rendering)
```

## 📥 Installation

### macOS Setup with Miniconda

1. **Install System Dependencies** (required for weasyprint)
   ```bash
   # Using Homebrew (install if needed: https://brew.sh)
   brew install python@3.10 cairo pango gdk-pixbuf libffi pkg-config
   ```

2. **Install Miniconda** (if not already installed)
   ```bash
   # Download and run the installer
   curl -O https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-arm64.sh
   bash Miniconda3-latest-MacOSX-arm64.sh
   
   # For Intel Macs, use MacOSX-x86_64.sh instead
   ```

3. **Create and Activate Virtual Environment**
   ```bash
   conda create -n takeout-to-pdf python=3.10
   conda activate takeout-to-pdf
   ```

4. **Install Python Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

### Other Platforms

**Prerequisites**

```bash
# Windows GTK3 Setup (required for weasyprint)
winget install -e --id TheMSYS2.MSYS2
pacman -S mingw-w64-x86_64-gtk3
```

**Package Installation**

```bash
uv venv  # Create virtual environment
uv install mailbox weasyprint beautifulsoup4 tqdm
```

## 🖥️ Usage

### Basic Usage

```bash
# Basic conversion (outputs to ./emails_combined.pdf)
python main.py --input ./takeout.mbox

# Custom output path (if filtering)
python main.py -i ./takeout.mbox -e your-email@example.com
```

### Filtering by Email Address

The `-e` or `--email` flag allows you to filter emails by a specific email address. The filter searches across all email fields (From, To, CC, BCC) and includes any email where the specified address appears:

```bash
# Filter emails involving client@company.com
python main.py -i ./takeout.mbox -e "client@company.com"
# Output: client@company.com.pdf

# Filter emails from a specific person
python main.py -i ./takeout.mbox -e "john@example.com"
# Output: john@example.com.pdf
```

**Filter Behavior:**
- Case-insensitive matching
- Includes emails where the specified address appears in any of these fields:
  - **From**: Emails sent by this address
  - **To**: Emails sent to this address
  - **CC**: Emails where this address was CC'd
  - **BCC**: Emails where this address was BCC'd
- Output filename is automatically set to `{email_address}.pdf`

### Exporting Emails from Google Takeout

1. **Target Selection**
   ```gmail
   from:client@company.com OR to:client@company.com after:2020/01/01
   ```
2. **Label & Export**
   - Create label `Export-ClientComms`
   - [Takeout Link](https://takeout.google.com/) → Gmail → Export by label

![Export Process](https://via.placeholder.com/600x400?text=Google+Takeout+Walkthrough)

## 💼 Use Cases

### 🏛 Legal Compliance

- **Audit Trails**: Bundle all client communications for discovery
- **Regulatory Proof**: Preserve timestamps and attachments

### 🔐 Personal Archival

```bash
# Create searchable family history archive
python main.py -i ./family_emails.mbox
```

### 🚚 Data Migration

- Prepare clean email bundles for Outlook/Thunderbird import
- Convert Gmail labels to PDF bookmark hierarchies

## 🤝 Contributing

We welcome improvements! Please follow our guidelines:

1. Fork repository
2. Create feature branch (`feat/pdf-optimization`)
3. Submit PR with tests
4. Review using `uv lint`

## 📜 License

MIT License - See [LICENSE](LICENSE) for full text
