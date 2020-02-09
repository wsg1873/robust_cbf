from video import make_video

import os
import pygame
import numpy as np
from math import sin, radians, degrees, copysign
from pygame.math import Vector2
import time

import osqp
import cvxpy
import scipy.sparse as sparse
import scipy as sp

kRandom = True
kDebug = False
kAvoid = True
kDraw = True
kVideo = False

zp_max = 0.05
zv_max = 0.05
zv2_max = 0.2


def filter_output(agent_idx, u_nom, agents, x_nom, T_bar=4):
    nx = 4
    nu = 2
    dt = agents[agent_idx].dt
    N_a = len(agents)

    x0 = np.array([agents[agent_idx].position[0], agents[agent_idx].position[1],
                   agents[agent_idx].velocity[0], agents[agent_idx].velocity[1]])
    A = agents[agent_idx].A
    Ax = A[0:2, :]
    Av = A[2:4, :]
    B = agents[agent_idx].B
    Bx = B[0:2, :]
    Bv = B[2:4, :]
    umax = agents[agent_idx].max_acceleration

    u_new = []
    u_diff = np.zeros((u_nom.shape[0]-1, 2))
    path_new = []
    # path_new.append(x0)
    bar_flag = False
    active_counter = -1
    for t in range(x_nom.shape[1]-1):
        if (bar_flag):
            active_counter += 1
            if (active_counter > T_bar):
                break

        # MPC Problem (u(0), x(1), lambda, eps)
        # Minimize actuation deviation
        duals = 12*(N_a-1)
        P = np.zeros((7 + duals, 7 + duals))
        P[2, 2] = 1
        P[3, 3] = 1
        P[4, 4] = 1
        P[5, 5] = 1
        P[6, 6] = agents[agent_idx].eps
        P = sparse.csc_matrix(P)
        q = np.hstack([0.0, 0.0, -x_nom[:, t+1], 0.0, np.zeros(duals)])

        # Velocity/Acceleration Constraints
        up = agents[agent_idx].max_velocity - np.matmul(Av, x0)
        up = np.hstack((up, agents[agent_idx].max_acceleration,
                        agents[agent_idx].max_acceleration))
        lp = -agents[agent_idx].max_velocity - np.matmul(Av, x0)
        lp = np.hstack((lp, -agents[agent_idx].max_acceleration, -
                        agents[agent_idx].max_acceleration))
        A_np = np.vstack([np.hstack([Bv, np.zeros((2, 4)), np.zeros((2, 1)), np.zeros((2, duals))]),
                          np.hstack([np.eye(2), np.zeros((2, 4)), np.zeros((2, 1)), np.zeros((2, duals))])])

        # Dynamics Constraint
        A_np = np.vstack([A_np, np.hstack([B, -np.eye(4), np.zeros((4, 1)), np.zeros((4, duals))])])
        lp = np.hstack((lp, -np.matmul(A, x0)))
        up = np.hstack((up, -np.matmul(A, x0)))

        # Barrier Constraint
        idx = 0
        for j in range(len(agents)):
            if (j == agent_idx):
                continue
            pd = np.array([x0[0] - (agents[j].position[0] + agents[j].velocity[0]*agents[j].dt*t),
                           x0[1] - (agents[j].position[1] + agents[j].velocity[1]*agents[j].dt*t)])
            vd = np.array([x0[2] - agents[j].velocity[0],
                           x0[3] - agents[j].velocity[1]])
            vd2 = vd  # Constant velocity assumption
            den = np.linalg.norm(pd + vd*dt) - (zp_max + zv_max*dt)
            den_p = np.linalg.norm(pd + vd*dt) + (zp_max + zv_max*dt)
            H1 = -np.hstack([ vd2 / den + ((agents[agent_idx].gamma - 1)*vd / (np.linalg.norm(pd) - zp_max)), vd2*dt / den + ((agents[agent_idx].gamma - 1)*pd / (np.linalg.norm(pd) - zp_max)), (pd + vd*dt) / den])
            H2 = -np.array([[dt / den, 0.0, (dt**2) / den, 0.0, 0.0, 0.0], [0.0, dt / den, 0.0, (dt**2) / den, 0.0, 0.0]])
            H3 = -np.hstack([dt*(pd + vd*dt) / den_p, 1.0])
            G = np.kron(np.eye(2), np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]))
            g = np.kron(np.ones(2), np.array([zp_max, zp_max, zv_max, zv_max, zv2_max, zv2_max]))
            h_l1 = np.expand_dims(np.hstack([H3[0:2], np.zeros(4), H3[2], np.zeros(12*idx), g, np.zeros(12*(len(agents)-2-idx))]), axis=0)
            h_l2 = np.hstack([np.transpose(H2), np.zeros((6, 4)), np.zeros((6,1)), np.zeros((6,12*idx)), -np.transpose(G), np.zeros((6, 12*(len(agents)-2-idx)))])
            h_r1 = min(np.dot(pd + vd*dt, vd2) / den, np.dot(pd + vd*dt, vd2) / den_p) + np.sqrt(max(2*umax*(np.linalg.norm(pd + vd*dt) - zp_max - zv_max*dt - agents[agent_idx].Ds), 0.0)) + (agents[agent_idx].gamma - 1)*np.sqrt(max(2*umax*(np.linalg.norm(pd) - zp_max - agents[agent_idx].Ds), 0.0)) + (agents[agent_idx].gamma - 1)*min(np.dot(pd, vd) / (np.linalg.norm(pd) + zp_max), np.dot(pd, vd) / (max(np.linalg.norm(pd) - zp_max, 0.001)))
            h_r2 = -H1
            A_np = np.vstack([A_np, h_l1, h_l2])
            lp = np.hstack((lp, -np.inf, h_r2 - 1.0*np.ones(6)))
            up = np.hstack((up, h_r1, h_r2 + 1.0*np.ones(6)))
            idx += 1

        h_l3 = np.hstack([np.zeros((duals, 6)), -np.eye(duals), np.zeros((duals, 1))])
        A_np = np.vstack([A_np, h_l3])
        lp = np.hstack((lp, -np.inf*np.ones(duals)))
        up = np.hstack((up, np.zeros(duals)))
        Ap = sparse.csc_matrix(A_np)
        '''
        # Slack variable
        A_np = np.vstack([A_np, np.zeros((1, A_np.shape[1]))])
        ab = np.zeros((A_np.shape[0], 1))
        ab[-1, 0] = 1
        for j in range(len(agents)-1):
            ab[-2-j, 0] = -1
        A_np = np.hstack([A_np, ab])
        Ap = sparse.csc_matrix(A_np)
        lp = np.hstack((lp, 0.0))
        up = np.hstack((up, np.inf))
        '''
        
        # Create an OSQP object
        # prob = osqp.OSQP()

        # Setup workspace and change alpha parameter
        if (t >= 0):
            prob = osqp.OSQP()
            prob.setup(P, q, Ap, lp, up, verbose=False)
        else:
            prob.update(q=q, l=lp, u=up)
            prob.update(Ax=Ap)
        # Solve problem
        res = prob.solve()
        if (t == 0):
            ctrl = res.x[0:2]
        u_out = res.x[0:2]
        
        u_new.append(u_out)
        u_diff[t, :] = u_out - u_nom[t, :]
        x0 = np.matmul(A, x0) + np.matmul(B, u_out)
        path_new.append(x0)
        if (np.linalg.norm(u_diff[t, :]) > 1.0):
            bar_flag = True

    # print("Output: " + str(u_new[0]))
    # print(h_const + np.dot(h_u, u_out) + res.x[2])
    # print("Residual: " + str(res.x[2]))
    return np.array(u_new), np.transpose(path_new), u_diff, bar_flag


def get_trajectory_avoid(agent, goal=None, N=8, agents=None, agent_idx=None):
    dt = agent.dt
    # Ad = sparse.csc_matrix([[1, 0, dt, 0], [0, 1, 0, dt],
    #                         [0, 0, 1, 0], [0, 0, 0, 1]])
    # Bd = sparse.csc_matrix([[0, 0], [0, 0], [dt, 0], [0, dt]])
    Ad = np.array([[1, 0, dt, 0], [0, 1, 0, dt],
                   [0, 0, 1, 0], [0, 0, 0, 1]])
    Bd = np.array([[0, 0], [0, 0], [dt, 0], [0, dt]])
    
    # Sizes
    nx = 4
    nu = 2

    # Constraints
    umin = -agent.max_acceleration*np.ones(nu)
    umax = agent.max_acceleration*np.ones(nu)
    xmin = np.array([-20, -20, -agent.max_velocity, -agent.max_velocity])
    xmax = np.array([100, 100, agent.max_velocity, agent.max_velocity])

    # Objective function
    Q = sparse.diags([10., 10., 1., 1., ])
    QN = Q
    R = 1.0*sparse.eye(nu)
    Qz = sparse.diags([0., 0.])

    # Initial and reference states
    x0 = np.array([agent.position[0], agent.position[1],
                   agent.velocity[0], agent.velocity[1]])
    if (goal is None):
        xg = np.array([agent.goal[0], agent.goal[1], 0.0, 0.0])
    else:
        xg = np.array([goal[0], goal[1], 0.0, 0.0])

    # Cast MPC problem to a QP: x = (x(0),x(1),...,x(N),u(0),...,u(N-1), eps)
    # - quadratic objective
    if (goal is None and agents is not None):
        P = sparse.block_diag([sparse.kron(sparse.eye(N), 0*Q), QN,
                               sparse.kron(sparse.eye(N), R), 100000.0]).tocsc()
        P = P[4:, 4:]
        # - linear objective
        q = np.hstack([np.kron(np.zeros(N), -Q.dot(xg)), -QN.dot(xg),
                       np.zeros((N)*nu), 0.0])
        q = q[4:]
    elif (goal is None and agents is None):
        P = sparse.block_diag([sparse.kron(sparse.eye(N), 0*Q), QN,
                               sparse.kron(sparse.eye(N), R)]).tocsc()
        P = P[4:, 4:]
        # - linear objective                                                                                                                                   
        q = np.hstack([np.kron(np.zeros(N), -Q.dot(xg)), -QN.dot(xg),
                       np.zeros((N)*nu)])
        q = q[4:]
    elif (goal is not None and agents is None):
        P = sparse.block_diag([sparse.kron(sparse.eye(N), Q), QN,
                               sparse.kron(sparse.eye(N), R)]).tocsc()
        P = P[4:, 4:]
        # - linear objective                                                                                                                                                            
        q = np.hstack([np.kron(np.ones(N), -Q.dot(xg)), -QN.dot(xg),
                       np.zeros((N)*nu)])
        q = q[4:]
    else:
        P = sparse.block_diag([sparse.kron(sparse.eye(N), Q), QN,
                               sparse.kron(sparse.eye(N), R), 100000.0]).tocsc()
        P = P[4:, 4:]
        # - linear objective
        q = np.hstack([np.kron(np.ones(N), -Q.dot(xg)), -QN.dot(xg),
                       np.zeros((N)*nu), 0.0])
        q = q[4:]

    # - linear dynamics
    Ax = np.kron(np.eye(N+1), -np.eye(nx)) + np.kron(np.eye(N+1, k=-1), Ad)
    Ax = Ax[4:, 4:]
    # Ax = sparse.hstack([Ax, sparse.csc_matrix((Ax.shape[0],1))])
    Bu = np.kron(np.vstack(
        [np.zeros((1, N)), np.eye(N)]), Bd)
    Bu = Bu[4:, :]
    # Bu = np.hstack([Bu, np.zeros((Bu.shape[0], 1))])

    # Dynamics constraints
    Aeq = np.hstack([Ax, Bu])
    leq = np.hstack([-x0[0]-dt*x0[2], -x0[1]-dt*x0[3], -
                     x0[2], -x0[3], np.zeros((N-1)*nx)])
    ueq = leq

    # - input and state constraints
    Aineq = np.eye((N)*nx + N*nu)
    lineq = np.hstack([np.kron(np.ones(N), xmin),
                       np.kron(np.ones(N), umin)])
    uineq = np.hstack([np.kron(np.ones(N), xmax),
                       np.kron(np.ones(N), umax)])
    A_vel = np.hstack([np.kron(np.eye(N), np.array([[0, 0, 1, 0], [
                          0, 0, 0, 1]])), np.kron(np.eye(N), np.zeros((2, 2)))])
    Aineq = np.vstack([Aineq, A_vel])
    lineq = np.hstack([lineq, np.kron(np.ones(N), np.array(
        [-agent.max_velocity, -agent.max_velocity]))])
    uineq = np.hstack([uineq, np.kron(np.ones(N), np.array(
        [agent.max_velocity, agent.max_velocity]))])

    A = np.vstack([Aeq, Aineq])
    l = np.hstack([leq, lineq])
    u = np.hstack([ueq, uineq])

    # Barrier Constraint
    if (agents is not None):
        for j in range(len(agents)):
            if (j == agent_idx):
                continue
            pd = np.array([x0[0] - (agents[j].position[0] + agents[j].velocity[0]*agents[j].dt),
                           x0[1] - (agents[j].position[1] + agents[j].velocity[1]*agents[j].dt)])
            vd = np.array([x0[2] - agents[j].velocity[0],
                           x0[3] - agents[j].velocity[1]])
            c = pd + vd*dt
            h_const = np.dot(c, vd) / np.linalg.norm(c) + np.sqrt(2*abs(umax[0])*(max(np.linalg.norm(c) - agents[agent_idx].Ds, 0))) - (1 - agents[agent_idx].gamma)*np.dot(
                pd, vd)/np.linalg.norm(pd) - (1 - agents[agent_idx].gamma)*np.sqrt(2*abs(umax[0])*(max(np.linalg.norm(pd) - agents[agent_idx].Ds, 0)))  # Ignore u_{user}
            h_u = c*dt/np.linalg.norm(c)
            ub = h_const
            Ab = -h_u
            A = np.vstack(
                [A, np.hstack([np.zeros((1, 4*N)), np.expand_dims(Ab, axis=0), np.zeros((1, 2*(N-1)))])])
            l = np.hstack((l, -np.inf))
            u = np.hstack((u, ub))

    if (agents is not None):
        # Slack variable                         
        A = np.vstack([A, np.zeros((1, A.shape[1]))])
        ab = np.zeros((A.shape[0], 1))
        ab[-1, 0] = 1
        for j in range(len(agents)-1):
            ab[-2-j, 0] = -1
        A = np.hstack([A, ab])
        l = np.hstack((l, 0.0))
        u = np.hstack((u, np.inf))

    A = sparse.csc_matrix(A)
        
    # Create an OSQP object
    prob = osqp.OSQP()

    # Setup workspace
    prob.setup(P, q, A, l, u, warm_start=True, verbose=False)

    # Simulate in closed loop
    nsim = 1

    # Store data Init
    xst = np.zeros((nx, nsim))
    ust = np.zeros((nu, nsim))

    # Solve
    res = prob.solve()

    # Check solver status
    if res.info.status != 'solved':
        raise ValueError('OSQP did not solve the problem!')

    # Apply first control input to the plant
    if (agents is not None):
        ctrl = res.x[-N*nu-1:-(N-1)*nu-1]
    else:
        ctrl = res.x[-N*nu:-(N-1)*nu]
    x0 = Ad.dot(x0) + Bd.dot(ctrl)

    i = 0
    # Store Data
    xst[:, i] = x0
    ust[:, i] = ctrl
    x_path = np.transpose(np.reshape(res.x[0:N*nx], (N, nx)))
    u_path = np.reshape(res.x[-N*nu-1:-1], (N, nu))
    # z_path = np.transpose(np.reshape(res.x[(N+1)*ns+N*nu:], (N+1, int(ns/2))))
    # Update initial state
    l[:nx] = -x0
    u[:nx] = -x0
    prob.update(l=l, u=u)

    return ctrl, x_path, u_path

# dt [x; y, xdot, ydot] = [0, 0, 1, 0; 0, 0, 0, 1; 0, 0, 0, 0; 0, 0, 0, 0] x + [0, 0; 0, 0; 1, 0;, 0, 1] u


class Car:
    def __init__(self, x, y, angle=0, length=4, max_steering=30, max_acceleration=2.0):
        self.position = Vector2(x, y)
        self.velocity = Vector2(0.0, 0.0)
        self.accel = 0.5
        self.length = length
        self.max_acceleration = max_acceleration
        self.max_velocity = 5
        self.speed = 10
        self.dt = 0.3

        self.A = np.array([[1, 0, self.dt, 0], [0, 1, 0, self.dt],
                           [0, 0, 1, 0], [0, 0, 0, 1]])
        self.B = np.array([[0, 0], [0, 0], [self.dt, 0], [0, self.dt]])
        self.Ac = np.array([[0, 0, 1, 0], [0, 0, 0, 1],
                            [0, 0, 0, 0], [0, 0, 0, 0]])
        self.Bc = np.array([[0, 0], [0, 0], [1, 0], [0, 1]])

        # Barrier
        self.Ds = 8.0
        self.gamma = 0.3
        self.eps = 1000000

    def update(self, u):
        self.position[0] += self.velocity.x * self.dt
        self.position[1] += self.velocity.y * self.dt

        self.velocity.x += self.dt*u[0]
        self.velocity.y += self.dt*u[1]
        self.velocity.x = max(-self.max_velocity,
                              min(self.velocity.x, self.max_velocity))
        self.velocity.y = max(-self.max_velocity,
                              min(self.velocity.y, self.max_velocity))

    def project(self, u):
        state = np.zeros(4)
        state[0] += self.velocity.x * self.dt
        state[1] += self.velocity.y * self.dt

        state[2] += self.dt*u[0]
        state[3] += self.dt*u[1]
        state[2] = max(-self.max_velocity, min(state[2], self.max_velocity))
        state[3] = max(-self.max_velocity, min(state[3], self.max_velocity))
        return state


class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Car tutorial")
        self.width = 1024*3
        self.height = 1024*3
        self.x_vel = 10
        self.screen = pygame.display.set_mode((self.width, self.height))
        self.clock = pygame.time.Clock()
        self.ticks = 60
        self.backImgScrollSpeed = 0
        self.exit = False

    def background():
        y = 10
        backImg = pygame.image.load('background.PNG')
        backImg = backImg.convert_alpha()
        backImgHeight = backImg.get_rect().height
        scrollY = y % backImgHeight
        # self.screen.blit(backImg, (0, backImgHeight))
        screen.blit(backImg, (0, scrollY - backImgHeight))

    def run(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        image_path = os.path.join(current_dir, "user.PNG")
        car_image = pygame.image.load(image_path)
        ppu = 32
        N_a = 25  # Number of agents
        agents = [0] * N_a
        agents_ctrl = [0] * N_a

        # Set horizon for each agent
        horizon_set = [0, 0, 6, 8, 8, 10]
        agents_avoid = [False] * N_a
        agent_positions = []
        for i in range(len(agents_avoid)):
            '''
            if (np.random.rand() < 0.5):
                agents_avoid[i] = True
            '''
            agents_avoid[i] = horizon_set[np.random.randint(len(horizon_set))]
            
        # Set up user and 2 agents
        dist = 0
        # user = Car(28, 30)
        # user.goal = np.random.rand(2)*60
        x1, y1 = 20*np.random.rand(), 20*np.random.rand()
        agent1 = Car(x1, y1)
        agent1.goal = np.array([40, 40]) + np.random.rand(2)*20
        agent1.max_acceleration = 10.0
        agent1.max_velocity = 3.0
        agent1.Ds = 6.0
        x1, y1 = 20*np.random.rand(), 20*np.random.rand()
        agents[0] = Car(x1, y1)
        agents[0].goal = np.array([40, 40]) + np.random.rand(2)*20
        agents[0].Ds = 7.0
        agents[0].max_acceleration = 10.0
        agents[0].max_velocity = 3.0
        agent_positions.append(np.array([x1, y1]))
        for i in range(1, N_a):
            start_collision = True
            while (start_collision):
                start_collision = False
                x1, y1 = 60*np.random.rand(), 60*np.random.rand()
                for j in range(len(agent_positions)):
                    dist = np.sqrt((x1 - agent_positions[j][0])**2 + (y1 - agent_positions[j][1])**2)
                    if (dist < 9.0):
                        start_collision = True
                        break
                    
            agent_positions.append(np.array([x1, y1]))
            agents[i] = Car(x1, y1)
            agents[i].goal = np.array(
                [60*np.random.rand(), 60*np.random.rand()])
            agents[i].Ds = 10.0
            agents[i].max_acceleration = 10.0
            agents[i].max_velocity = 3.0
        if (not kRandom):
            agent1.position[0] = 20
            agent1.goal[0] = 20
            agent2.position[0] = 20
            agent2.goal[0] = 20
            agent1.position[1] = 20
            agent2.position[1] = 50
            agent2.Ds = 8.0
            agent2.max_acceleration = 9.0
            agent2.max_velocity = 3.0

        # Initiate video generator
        save_screen = make_video(self.screen)
        
        u1 = np.zeros(2)
        u2 = np.zeros(2)
        counter = 0
        ctrl1_history = []
        ctrl2_history = []
        while not self.exit:
            dt = agent1.dt

            # Event queue
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.exit = True

            # User input
            pressed = pygame.key.get_pressed()

            start_time = time.time()
            ''' GREEDY STRATEGY
            u, path, u_path = get_trajectory_avoid(agents[0], agents=agents, agent_idx=0)
            path_safe = path
            path_smooth = path
            agents_ctrl[0] = u
            '''
            u, path, u_path = get_trajectory_avoid(agents[0])  
            u1, path_safe, u_diff, bar_flag = filter_output(
                0, u_path, agents, path)
            path_smooth = path_safe
            # if (bar_flag):
            #     _, path_smooth, u1 = get_trajectory_avoid(
            #         agents[0], goal=path_safe[:, -1], agents=agents, agent_idx=0)
            agents_ctrl[0] = u1[0, :]
            
            print("Solve time: " + str(time.time() - start_time))
            for j in range(1, N_a):
                '''  GREEDY STRATEGY
                u2, path2, u2_path = get_trajectory_avoid(agents[j], N=10, agents=agents, agent_idx=j)
                path_smooth2 = path2
                agents_ctrl[j] = u2
                '''
                if (agents_avoid[j] > 0):
                    u2, path2, u2_path = get_trajectory_avoid(agents[j], N=agents_avoid[j])
                    u2, path2_safe, _, bar_flag2 = filter_output(
                        j, u2_path, agents, path2)
                    if (bar_flag2):
                        _, path_smooth2, u2 = get_trajectory_avoid(
                            agents[j], goal=path2_safe[:, -1], agents=agents, agent_idx=j)
                    agents_ctrl[j] = u2[0, :]
                else:
                    u2, path2, u2_path = get_trajectory_avoid(agents[j], N=8)
                    agents_ctrl[j] = u2
            # Logic
            noise_a = 0.0
            u_no = np.zeros(2)
            # user.update(u_no)
            for j in range(N_a):
                agents[j].update(agents_ctrl[j] + noise_a *
                                 (np.random.rand(2) - 0.5))

            # ctrl1_history.append(ctrl_1)
            # ctrl2_history.append(ctrl_2)

            # Drawing
            self.screen.fill((0, 0, 0))

            agent1_img = pygame.image.load('agents.jpg')
            rect = agent1_img.get_rect()
            self.screen.blit(agent1_img, agents[0].position *
                             ppu - (rect.width / 2, rect.height / 2))

            agent1_goal = pygame.image.load('agents.jpg')
            rect = agent1_goal.get_rect()
            self.screen.blit(agent1_goal, agents[0].goal * ppu -
                             (rect.width / 2, rect.height / 2))

            for j in range(1, N_a):
                agent2_img = pygame.image.load('user.PNG')
                rect = agent2_img.get_rect()
                self.screen.blit(
                    agent2_img, agents[j].position * ppu - (rect.width / 2, rect.height / 2))
                # agent2_goal = pygame.image.load('user.PNG')
                # rect = agent2_goal.get_rect()
                # self.screen.blit(agent2_goal, agent2.goal * ppu -
                #                  (rect.width / 2, rect.height / 2))

            if (kDraw):
                for i in range(path_safe.shape[1]):
                    pygame.draw.circle(self.screen, (0, 0, 255), (int(
                        ppu*path_safe[0, i]), int(ppu*path_safe[1, i])), int(ppu/2))
                if (bar_flag):
                    for i in range(path_smooth.shape[1]):
                        pygame.draw.circle(self.screen, (0, 255, 0), (int(
                            ppu*path_smooth[0, i]), int(ppu*path_smooth[1, i])), int(ppu/2))
            pygame.display.flip()
            time.sleep(0.01)
            self.clock.tick(self.ticks)
            counter += 1
            if (kVideo):
                next(save_screen)
        pygame.quit()


if __name__ == '__main__':
    game = Game()
    print("Game Initialized")
    game.run()
