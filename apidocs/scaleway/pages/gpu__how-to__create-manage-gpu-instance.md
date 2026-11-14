---
title: How to create and manage a GPU Instance
description: This page explains how to create and manage a GPU Instance
tags: gpu-instance gpu instance
dates:
  validation: 2025-09-17
  posted: 2022-03-25
---

Scaleway GPU Instances feature dedicated high-end Nvidia GPUs, making them optimal for tasks such as data processing, artificial intelligence, rendering, and video encoding.

Once you've created your GPU Instance, [connecting to it via SSH](/en/docs/instances/how-to/connect-to-instance/) allows you to use our [pre-configured Docker images](/en/docs/gpu/how-to/use-gpu-with-docker/), providing instant access to a [preinstalled environment](/en/docs/gpu/how-to/use-preinstalled-env/) with your preferred AI libraries and tools already set up.

Moreover, GPU Instances offer all the functionalities of our standard Instances, including [flexible IPs](/en/docs/instances/how-to/use-flexips/), [Security Groups](/en/docs/instances/how-to/use-security-groups/), [Private Networks](/en/docs/instances/how-to/use-private-networks/), backups, and more.

When you have completed your calculations using the GPU Instance, [deletion can be done through the Scaleway console](/en/docs/instances/how-to/delete-instance/), API, or our CLI tools.

## Before you start

To complete the following actions, you must have:

* A Scaleway account logged into the [console](https://console.scaleway.com)
* [Owner](/en/docs/iam/concepts/glossary/#owner) status or [IAM permissions](/en/docs/iam/concepts/glossary/#permission) allowing you to perform actions in the intended Organization
* An [SSH key](/en/docs/iam/security/create-ssh-key/) added to your account

## How to create a GPU Instance

1. Click **CPU & GPU Instances** in the **Compute** section of the side menu. The [Instance dashboard](https://console.scaleway.com/instance/servers) displays.

2. Click **Create Instance**, then select **Create GPU Instance**. The Instance creation wizard displays.

3. Select an Instance range based on your usage. Instance offers vary in pricing, processing power, memory, storage, and bandwidth. [Discover the best Instance type for your needs](/en/docs/instances/reference-content/choosing-instance-type/).

   > **Tip**
   >
   > Click **Instance view** > **List** to view a list of all available Instances.

4. Choose an Instance configuration from the list and click the select icon. A pop-up displays.

5. Select the Availability Zone in which you want to deploy your Instance. Then click **Configure Instance**.

6. Complete the following steps:

   * **Name your Instance**, or leave the randomly-generated name in place. Optionally, you can add [tags](/en/docs/instances/concepts/#tags) to help you organize your Instance.

   * **Image**: Pick an operating system image suitable for your GPU Instance. For example, select **Ubuntu Noble GPU OS 13 (NVIDIA)**, which comes with preinstalled NVIDIA drivers and an NVIDIA Docker environment. You can customize your working environment using Docker with our provided Docker images or your own containers.

   * **Add volumes**, which are storage spaces used by your Instances. A block volume with a default name and 5,000 IOPS is automatically provided for your system volume. You can customize this volume and attach up to 16 local and/or block type volumes as needed.

     > **Important**
     >
     > * Ensure that the volume containing your OS image has a minimum size of 10 GB. For a GPU OS, the recommended size is 125 GB.
     > * When multiple Block Storage volumes are linked to your Instance, the primary volume will host the OS and is essential for booting the Instance. If needed, you can [change the boot volume after the Instance creation](/en/docs/instances/how-to/use-boot-modes/#how-to-change-the-boot-volume).
     > * Booting from a volume that either lacks an OS or is among multiple volumes with identical operating systems can lead to inconsistent boot outcomes.

   * **Configure network** of the Instance.

     * Leave the checkbox ticked to assign a **Public IPv4** to the Instance. You can either allocate a new IPv4 address or select one or multiple existing IPv4s. Alternatively, uncheck the box if you do not want an IPv4.

     * Leave the checkbox ticked to assign a **Public IPv6** to the Instance. You can either allocate a new IPv6 address or select one or multiple existing IPv6s. Alternatively, uncheck the box if you do not want an IPv6.

       > **Note**
       >
       > You can attach up to 5 IPv4 and 5 IPv6 addresses to an Instance, which is useful for running different services or applications on the same Instance.

   * (Optional) Click **Cloud-init** to set up a [cloud-init configuration](/en/docs/instances/concepts/#cloud-init). Otherwise, leave this option set to its default values. Cloud-init scripts are the standard way to automate Instance setup, such as setting up software, users, and system configurations at the first boot.

   * **Verify the [SSH keys](/en/docs/organizations-and-projects/concepts/glossary/#ssh-key)** that will give you access to your Instance.

7. Click **Review** to see a order summary. Verify the estimated cost of your Instance, based on the specifications you chose.

8. Click **Create Instance**. The creation of your Instance begins, and you will be informed when the Instance is ready.

## How to connect to a GPU Instance

See our documentation on [how to connect to your Instance via SSH](/en/docs/instances/how-to/connect-to-instance/).

Once you have connected via SSH, you can [launch a Docker container](/en/docs/gpu/how-to/use-gpu-with-docker/) to start working on your AI projects.

## How to use Instance features

For instructions on using any type of GPU Instance feature, including flexible IPs, placement groups, Private Networks, backups, and much more, check out our full [Instance how-to documentation](/en/docs/instances/how-to/).

## How to delete a GPU Instance

See our documentation on [how to delete your Instance](/en/docs/instances/how-to/delete-instance/).
