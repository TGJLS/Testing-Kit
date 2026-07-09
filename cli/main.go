package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

var (
	version   = "main"
	repoOwner = "TGJLS/Testing-Kit"
)

const apiURL = "http://localhost:1234"

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(1)
	}
	switch os.Args[1] {
	case "install":
		cmdInstall()
	case "up":
		cmdCompose("up", "-d")
	case "down":
		cmdCompose("down")
	case "reset":
		cmdCompose("down", "-v")
	case "run-tests":
		cmdRunTests()
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", os.Args[1])
		usage()
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "Usage: testing-kit-cli <command>")
	fmt.Fprintln(os.Stderr, "Commands: install, up, down, reset, run-tests")
}

func die(msg string) {
	fmt.Fprintf(os.Stderr, "✗ %s\n", msg)
	os.Exit(1)
}

func cmdCompose(args ...string) {
	cmd := exec.Command("docker", append([]string{"compose"}, args...)...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "docker compose failed: %v\n", err)
		os.Exit(1)
	}
}

func cmdRunTests() {
	resp, err := http.Post(apiURL+"/v1/run-tests", "application/json", nil)
	if err != nil {
		die(fmt.Sprintf("Error calling API: %v", err))
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	var result struct {
		Passed         int `json:"passed"`
		Failed         int `json:"failed"`
		TimedOut       int `json:"timed_out"`
		DispatchFailed int `json:"dispatch_failed"`
		Xfail          int `json:"xfail"`
		Results        []struct {
			Cmdline string `json:"cmdline"`
			Status  string `json:"status"`
			Output  string `json:"output"`
			ErrMsg  string `json:"err_msg"`
		} `json:"results"`
	}

	if err := json.Unmarshal(body, &result); err != nil {
		die(fmt.Sprintf("Error parsing API response: %v", err))
	}

	for _, r := range result.Results {
		icon := "✓"
		if r.Status == "failed" || r.Status == "timed-out" || r.Status == "dispatch-failed" {
			icon = "✗"
		} else if r.Status == "xfail" {
			icon = "⚠"
		}
		fmt.Printf("  %s [%s] %s\n", icon, r.Status, r.Cmdline)
		if r.Output != "" && r.Status != "passed" {
			fmt.Printf("    %s\n", r.Output)
		}
		if r.ErrMsg != "" {
			fmt.Printf("    err: %s\n", r.ErrMsg)
		}
	}

	fmt.Printf("\n%d passed, %d failed, %d timed out, %d dispatch-failed, %d xfail\n",
		result.Passed, result.Failed, result.TimedOut, result.DispatchFailed, result.Xfail)

	if result.Failed+result.TimedOut+result.DispatchFailed > 0 {
		os.Exit(1)
	}
}

func cmdInstall() {
	checkSoftware()
	downloadFiles()
	checkBtrfs()
	generateSSHKey()
	downloadOpenSSH()
	cmdCompose("up", "-d")
}

func checkSoftware() {
	fmt.Println("Checking prerequisites...")
	if _, err := exec.LookPath("docker"); err != nil {
		die("docker not found in PATH — install Docker first")
	}
	cmd := exec.Command("docker", "compose", "version")
	cmd.Stdout = io.Discard
	cmd.Stderr = io.Discard
	if err := cmd.Run(); err != nil {
		die("docker compose (v2) not available — install docker-compose-plugin or Docker Desktop")
	}
	fmt.Println("✓ docker and docker compose found")
}

func rawURL(path string) string {
	return fmt.Sprintf("https://raw.githubusercontent.com/%s/%s/%s", repoOwner, version, path)
}

func downloadToFile(url, dest string) error {
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d fetching %s", resp.StatusCode, url)
	}
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	return os.WriteFile(dest, data, 0o644)
}

func downloadFiles() {
	fmt.Println("Checking KVM availability...")
	composeSource := "docker-compose.yml"
	if _, err := os.Stat("/dev/kvm"); err == nil {
		fmt.Println("✓ /dev/kvm found — using KVM-enabled compose file")
		composeSource = "docker-compose.kvm.yml"
	} else {
		fmt.Println("⚠  /dev/kvm not found — using standard compose file (software emulation)")
	}

	fmt.Printf("Downloading docker-compose.yml from %s @ %s...\n", repoOwner, version)
	if err := downloadToFile(rawURL(composeSource), "docker-compose.yml"); err != nil {
		die(fmt.Sprintf("Failed to download compose file: %v", err))
	}
	fmt.Println("✓ docker-compose.yml downloaded")

	for _, f := range []string{"config/config.yaml", "config/tasks.yaml"} {
		if _, err := os.Stat(f); err == nil {
			fmt.Printf("⚠  %s already exists — skipping\n", f)
			continue
		}
		if err := downloadToFile(rawURL(f), f); err != nil {
			die(fmt.Sprintf("Failed to download %s: %v", f, err))
		}
		fmt.Printf("✓ %s downloaded\n", f)
	}
}

func checkBtrfs() {
	fmt.Println("Checking filesystem...")
	out, err := exec.Command("docker", "info", "--format", "{{.DockerRootDir}}").Output()
	if err != nil {
		fmt.Println("⚠  Could not determine Docker root — skipping btrfs check")
		return
	}
	dockerRoot := strings.TrimSpace(string(out))

	fsOut, err := exec.Command("stat", "-f", "-c", "%T", dockerRoot).Output()
	if err != nil || strings.TrimSpace(string(fsOut)) != "btrfs" {
		fmt.Println("✓ Not btrfs — skipping copy-on-write adjustment")
		return
	}

	fmt.Println("btrfs detected — checking copy-on-write on windows-data volume...")
	exec.Command("docker", "volume", "create", "testing-kit_windows-data").Run() //nolint:errcheck

	attrOut, _ := exec.Command("docker", "run", "--rm", "--cap-add", "LINUX_IMMUTABLE",
		"-v", "testing-kit_windows-data:/data", "alpine", "sh", "-c",
		"apk add --no-cache e2fsprogs-extra >/dev/null 2>&1; lsattr -d /data").Output()

	if strings.Contains(string(attrOut), "C") {
		fmt.Println("✓ Copy-on-write already disabled on testing-kit_windows-data")
		return
	}

	exec.Command("docker", "run", "--rm", "--cap-add", "LINUX_IMMUTABLE", //nolint:errcheck
		"-v", "testing-kit_windows-data:/data", "alpine", "sh", "-c",
		"apk add --no-cache e2fsprogs-extra >/dev/null 2>&1; chattr +C /data").Run()
	fmt.Println("✓ Disabled copy-on-write on testing-kit_windows-data")
}

func downloadOpenSSH() {
	const dest = "windows/oem/OpenSSH-Win64.zip"
	const url = "https://github.com/PowerShell/Win32-OpenSSH/releases/latest/download/OpenSSH-Win64.zip"

	if _, err := os.Stat(dest); err == nil {
		fmt.Printf("⚠  %s already exists — skipping\n", dest)
		return
	}
	fmt.Println("Downloading OpenSSH-Win64.zip for Windows VM setup...")
	if err := downloadToFile(url, dest); err != nil {
		die(fmt.Sprintf("Failed to download OpenSSH-Win64.zip: %v", err))
	}
	fmt.Printf("✓ OpenSSH-Win64.zip downloaded to %s\n", dest)
}

func generateSSHKey() {
	const keyPath = "ssh/id_test"
	const pubPath = "ssh/id_test.pub"

	if _, err := os.Stat(keyPath); err != nil {
		fmt.Println("Generating SSH keypair...")
		if err := os.MkdirAll("ssh", 0o700); err != nil {
			die(fmt.Sprintf("Failed to create ssh/ directory: %v", err))
		}
		cmd := exec.Command("ssh-keygen", "-t", "ed25519", "-N", "", "-f", keyPath, "-C", "testing-kit-ci")
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			die(fmt.Sprintf("ssh-keygen failed: %v", err))
		}
		if err := os.Chmod(keyPath, 0o600); err != nil {
			die(fmt.Sprintf("Failed to set key permissions: %v", err))
		}
		fmt.Println("✓ SSH keypair generated at ssh/id_test")
	} else {
		fmt.Println("⚠  ssh/id_test already exists — skipping key generation")
	}

	tmpl, err := os.ReadFile("windows/oem/install.bat.template")
	if err != nil {
		die(fmt.Sprintf("Failed to read windows/oem/install.bat.template: %v", err))
	}
	pubKey, err := os.ReadFile(pubPath)
	if err != nil {
		die(fmt.Sprintf("Failed to read %s: %v", pubPath, err))
	}
	rendered := strings.ReplaceAll(string(tmpl), "{{PUBLIC_KEY}}", strings.TrimSpace(string(pubKey)))
	if err := os.WriteFile("windows/oem/install.bat", []byte(rendered), 0o644); err != nil {
		die(fmt.Sprintf("Failed to write windows/oem/install.bat: %v", err))
	}
	fmt.Println("✓ windows/oem/install.bat rendered")
}
